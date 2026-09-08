from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse


_PLACEHOLDER = re.compile(r"(^https?://\.\.?/?$)|(\.\.\.)|(example\.com)|(<url>)|(\byour[-_]?site\b)", re.I)


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
    # reject empty path-only junk like https:///
    if not parsed.netloc:
        return None
    # rebuild without fragments; keep query
    clean = urlunparse(
        (parsed.scheme or default_scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, "")
    )
    return clean


def is_fetchable_url(url: Optional[str]) -> bool:
    return normalize_url(url) is not None


_NEXT_TEXT = {"next", "next page", "next ›", "»", "older", "more results", "load more"}
_PAGE_PARAM_RE = re.compile(r"[?&]page=(\d+)")
_PATH_PAGE_RE = re.compile(r"/(\d+),([^/?]*)$")  # e.g. ".../continentcodes=150/1,relevance,search.html"


def guess_next_page(current_url: str, soup) -> Optional[str]:  # soup: bs4.BeautifulSoup
    """Find the next listing page without needing a site-specific CSS selector.

    Tries, in order: the standard rel="next" link, a link whose visible text/aria-label says
    "next", a link whose own "page=" query param is exactly one past the current page, and a
    link whose last path segment is a leading page number one past the current one (Mascus's
    own search URLs use ".../N,relevance,search.html" rather than a query param). This covers
    the large majority of listing sites mechanically — no AI guess needed.
    """
    nxt = soup.select_one('a[rel="next"]')
    if nxt and nxt.get("href"):
        return urljoin(current_url, nxt["href"])

    for a in soup.select("a[href]"):
        label = (a.get("aria-label") or "").strip().lower()
        text = a.get_text(strip=True).lower()
        if label in _NEXT_TEXT or text in _NEXT_TEXT:
            return urljoin(current_url, a["href"])

    current_page = 1
    qs = parse_qs(urlparse(current_url).query)
    if "page" in qs:
        try:
            current_page = int(qs["page"][0])
        except ValueError:
            pass
    target = current_page + 1
    for a in soup.select("a[href*='page=']"):
        href = a.get("href") or ""
        m = _PAGE_PARAM_RE.search(href)
        if m and int(m.group(1)) == target:
            return urljoin(current_url, href)

    path_match = _PATH_PAGE_RE.search(urlparse(current_url).path)
    if path_match:
        current_path_page = int(path_match.group(1))
        rest = path_match.group(2)
        target_segment = f"{current_path_page + 1},{rest}"
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            if href.rstrip("/").endswith(target_segment):
                return urljoin(current_url, href)

    return None
