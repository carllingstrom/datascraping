from __future__ import annotations

import re
from typing import Optional, Set
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse


_PLACEHOLDER = re.compile(
    r"(^https?://\.\.?/?$)|(\.\.\.)|(example\.com)|(<url>)|(\byour[-_]?site\b)",
    re.I,
)

# Common listing pagination query keys across languages / frameworks
_PAGE_QUERY_KEYS = (
    "page",
    "curpage",
    "p",
    "pg",
    "sida",  # Swedish
    "pagina",
    "pagenumber",
    "page_number",
    "pageno",
    "pageNum",
)

# Visible / aria labels that usually mean "go to next page"
_NEXT_TEXT = {
    "next",
    "next page",
    "next ›",
    "›",
    "»",
    ">",
    "older",
    "more",
    "more results",
    "load more",
    "nästa",
    "nästa sida",
    "följande",
    "weiter",
    "suivant",
    "siguiente",
    "avanti",
}

# Path forms like ".../2,relevance,search.html" or ".../traktorer,2,relevance,search.html"
_PATH_PAGE_SLASH_RE = re.compile(r"/(\d+),([^/?#]*)$")
_PATH_PAGE_COMMA_RE = re.compile(r"/([^/?#]+?),(\d+),([^/?#]*)$")
_PATH_PAGE_SEGMENT_RE = re.compile(r"/page[/-](\d+)(?:/|$)", re.I)


def normalize_url(url: Optional[str], default_scheme: str = "https") -> Optional[str]:
    if url is None:
        return None
    text = str(url).strip()
    if not text:
        return None
    if text.startswith("//"):
        text = f"{default_scheme}:{text}"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = f"{default_scheme}://{text}"

    parsed = urlparse(text)
    host = (parsed.hostname or "").strip(".")
    if not host or host in {".", ".."} or ".." in host or host.startswith("."):
        return None
    if _PLACEHOLDER.search(text):
        return None
    if not parsed.netloc:
        return None
    clean = urlunparse(
        (
            parsed.scheme or default_scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        )
    )
    return clean


def is_fetchable_url(url: Optional[str]) -> bool:
    return normalize_url(url) is not None


def merge_query_params(current_url: str, next_url: str) -> str:
    """Carry forward listing filters (q=, category=, …) when a pager drops them.

    Many sites paginate with ?curpage=2 / ?page=2 and drop the original search query.
    If next shares the same path (or is a page-segment of it), keep current query keys
    that next omitted — except page keys, which must come from next.
    """
    cur = urlparse(current_url)
    nxt = urlparse(next_url)
    if cur.netloc != nxt.netloc:
        return next_url

    cur_path = cur.path.rstrip("/")
    nxt_path = nxt.path.rstrip("/")
    related = (
        cur_path == nxt_path
        or nxt_path.startswith(cur_path + ",")
        or nxt_path.startswith(cur_path + "/")
    )
    if not related:
        return next_url

    cur_qs = parse_qs(cur.query, keep_blank_values=True)
    nxt_qs = parse_qs(nxt.query, keep_blank_values=True)
    page_keys_lower = {k.lower() for k in _PAGE_QUERY_KEYS}

    merged = {
        k: v
        for k, v in cur_qs.items()
        if k.lower() not in page_keys_lower
    }
    merged.update(nxt_qs)
    query = urlencode(merged, doseq=True)
    return urlunparse((nxt.scheme, nxt.netloc, nxt.path, nxt.params, query, ""))


def _page_from_query(url: str) -> Optional[int]:
    qs = parse_qs(urlparse(url).query)
    for key in _PAGE_QUERY_KEYS:
        # case-insensitive key match
        for qk, values in qs.items():
            if qk.lower() == key.lower() and values:
                try:
                    return int(values[0])
                except ValueError:
                    continue
    return None


def _page_from_path(url: str) -> Optional[int]:
    path = urlparse(url).path
    m = _PATH_PAGE_SLASH_RE.search(path)
    if m:
        return int(m.group(1))
    m = _PATH_PAGE_COMMA_RE.search(path)
    if m:
        return int(m.group(2))
    m = _PATH_PAGE_SEGMENT_RE.search(path)
    if m:
        return int(m.group(1))
    return None


def current_page_number(url: str) -> int:
    return _page_from_query(url) or _page_from_path(url) or 1


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc == urlparse(b).netloc


def _listing_stem(url: str) -> str:
    """Strip known page tokens so page-1 and page-2 share a comparable stem."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    path = _PATH_PAGE_SLASH_RE.sub("", path)
    path = _PATH_PAGE_COMMA_RE.sub(r"/\1", path)  # keep category, drop ,N,suffix
    path = _PATH_PAGE_SEGMENT_RE.sub("/", path)
    path = re.sub(r"/+$", "", path)
    return f"{parsed.netloc}{path}".lower()


def _looks_like_page_href(href: str, target: int) -> bool:
    if re.search(rf"[?&](?:{'|'.join(_PAGE_QUERY_KEYS)})={target}(?:&|$)", href, re.I):
        return True
    if re.search(rf"/{target},[^/?#]*$", href):
        return True
    if re.search(rf",[ ]*{target},", href):  # category,2,relevance...
        return True
    if re.search(rf"/page[/-]{target}(?:/|$)", href, re.I):
        return True
    return False


def guess_next_page(current_url: str, soup) -> Optional[str]:  # soup: bs4.BeautifulSoup
    """Find the next listing page without a site-specific CSS selector.

    Strategy (general, not site-tied):
    1. rel="next"
    2. Link text / aria-label in a multilingual "next" set (incl. >, ›, nästa, …)
    3. Link that clearly targets current_page+1 via common query/path page patterns
    4. Link whose visible text is exactly the next page number, same listing family
    """
    current = normalize_url(current_url) or current_url
    target = current_page_number(current) + 1
    stem = _listing_stem(current)

    nxt = soup.select_one('a[rel="next"]')
    if nxt and nxt.get("href"):
        return merge_query_params(current, urljoin(current, nxt["href"]))

    for a in soup.select("a[href]"):
        label = (a.get("aria-label") or "").strip().lower()
        text = a.get_text(strip=True).lower()
        if label in _NEXT_TEXT or text in _NEXT_TEXT:
            return merge_query_params(current, urljoin(current, a["href"]))

    # Prefer hrefs that encode the target page number in a known pattern
    candidates = []
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(current, href)
        if not _same_host(current, absolute):
            continue
        text = a.get_text(strip=True)
        score = 0
        if _looks_like_page_href(href, target) or _looks_like_page_href(absolute, target):
            score += 3
        if text == str(target):
            score += 2
        if score and _listing_stem(absolute) == stem:
            score += 2
        elif score and (
            _listing_stem(absolute).startswith(stem)
            or stem.startswith(_listing_stem(absolute))
        ):
            score += 1
        if score >= 3:
            candidates.append((score, absolute))

    if candidates:
        candidates.sort(key=lambda x: (-x[0], len(x[1])))
        return merge_query_params(current, candidates[0][1])

    # Last resort: any same-host href that looks like page=target, even if stem differs slightly
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        absolute = urljoin(current, href)
        if not _same_host(current, absolute):
            continue
        if _page_from_query(absolute) == target or _page_from_path(absolute) == target:
            if _listing_stem(absolute).startswith(stem[: max(8, len(stem) // 2)]):
                return merge_query_params(current, absolute)

    return None


def extract_declared_total(soup) -> Optional[int]:
    """Best-effort total result count from embedded JSON (Next.js etc.)."""
    import json

    tag = soup.select_one("script#__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        data = json.loads(tag.string)
    except json.JSONDecodeError:
        return None

    found: Set[int] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"totalResults", "total", "totalCount", "resultCount", "nbHits"} and isinstance(
                    value, int
                ):
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node[:30]:
                walk(item)

    walk(data)
    return max(found) if found else None
