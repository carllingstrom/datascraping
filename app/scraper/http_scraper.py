from __future__ import annotations

from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models import FieldSpec, SiteSpec
from app.scraper.extract import extract_page_rows, meta_fallback, _year_from_text
from app.scraper.urls import extract_declared_total, guess_next_page, normalize_url


class HttpScraper:
    def __init__(self, timeout: Optional[int] = None) -> None:
        self.timeout = timeout or settings.request_timeout
        self.headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }
        # Filled during scrape_site — engine/CLI can surface coverage warnings
        self.last_declared_totals: List[Tuple[str, int]] = []

    def fetch(self, url: str, client: Optional[httpx.Client] = None) -> BeautifulSoup:
        clean = normalize_url(url)
        if not clean:
            raise ValueError(
                f"Invalid or placeholder URL: {url!r}. "
                "Use a real URL like https://www.klaravik.se/auktion/"
            )
        if client is not None:
            resp = client.get(clean)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=self.headers) as owned:
            resp = owned.get(clean)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")

    def scrape_site(
        self,
        site: SiteSpec,
        fields: List[FieldSpec],
        max_pages: int,
    ) -> List[dict]:
        raw_urls = [site.start_url] + list(site.extra_urls)
        urls: List[str] = []
        for u in raw_urls:
            clean = normalize_url(u)
            if not clean:
                raise ValueError(
                    f"Site '{site.name}' has invalid start URL {u!r}. "
                    "Replace placeholders like https://... with real URLs."
                )
            urls.append(clean)

        collected: List[dict] = []
        visited: Set[str] = set()
        self.last_declared_totals = []

        with httpx.Client(
            timeout=self.timeout, follow_redirects=True, headers=self.headers
        ) as client:
            for start in urls:
                page_url: Optional[str] = start
                for page_idx in range(max_pages):
                    if not page_url or page_url in visited:
                        break
                    visited.add(page_url)
                    soup = self.fetch(page_url, client=client)

                    if page_idx == 0:
                        declared = extract_declared_total(soup)
                        if declared:
                            self.last_declared_totals.append((site.name, declared))

                    rows = self._extract_page(soup, page_url, site, fields)
                    for row in rows:
                        row.setdefault("_site", site.name)
                        row.setdefault("_page", page_url)
                    collected.extend(rows)

                    if not rows:
                        break

                    next_url = None
                    if site.pagination_selector:
                        nxt = soup.select_one(site.pagination_selector)
                        href = nxt.get("href") if nxt else None
                        if href:
                            candidate = normalize_url(urljoin(page_url, href))
                            if candidate and candidate not in visited:
                                next_url = candidate
                    else:
                        guessed = guess_next_page(page_url, soup)
                        if guessed:
                            candidate = normalize_url(guessed)
                            if candidate and candidate not in visited:
                                next_url = candidate
                    page_url = next_url

        needs_year = any(f.name in {"model_year", "year", "årsmodell"} for f in fields)
        if needs_year:
            collected = self._enrich_years(collected)

        return collected

    def _extract_page(
        self,
        soup: BeautifulSoup,
        page_url: str,
        site: SiteSpec,
        fields: List[FieldSpec],
    ) -> List[dict]:
        rows, _source = extract_page_rows(soup, page_url, site.list_selector, fields)
        return rows

    def _enrich_years(self, rows: List[dict]) -> List[dict]:
        """Fill missing years from detail pages. Cap is configurable, not a magic 80."""
        limit = settings.enrich_detail_limit
        pending = [
            r
            for r in rows
            if r.get("url")
            and not r.get("model_year")
            and normalize_url(str(r.get("url")))
        ]
        if limit > 0:
            pending = pending[:limit]
        if not pending:
            return rows

        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=self.headers) as client:
            for row in pending:
                url = normalize_url(str(row["url"]))
                if not url:
                    continue
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                except Exception:  # noqa: BLE001
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                meta = meta_fallback(soup, url)
                year = meta.get("model_year") or _year_from_text(
                    soup.get_text(" ", strip=True)[:4000]
                )
                if year:
                    row["model_year"] = year
                if not row.get("price") and meta.get("price"):
                    row["price"] = meta["price"]
        return rows
