from __future__ import annotations

from typing import List, Optional, Tuple

from app.config import settings
from app.models import ScrapePlan, SiteSpec
from app.scraper.extract import apply_filters, merge_field_map
from app.scraper.http_scraper import HttpScraper


class ScrapeEngine:
    """Execute a ScrapePlan with HTTP (default) and Playwright when needed."""

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self.http = HttpScraper()

    def run(
        self,
        plan: ScrapePlan,
        credentials_by_site: Optional[dict] = None,
    ) -> List[dict]:
        credentials_by_site = credentials_by_site or {}
        all_rows: List[dict] = []

        needs_browser = any(
            site.method == "browser" or site.login is not None for site in plan.sites
        )
        browser = None
        if needs_browser:
            from app.scraper.browser_scraper import BrowserScraper

            browser = BrowserScraper(headless=self.headless)
            browser.__enter__()

        try:
            for site in plan.sites:
                fields = plan.resolved_fields_for(site)
                # Keep global field names too (e.g. model_year) for enrichment decisions
                enrich_fields = fields + [
                    f for f in plan.fields if f.name not in {x.name for x in fields}
                ]
                max_pages = site.max_pages or settings.max_pages_default
                rows = self._scrape_one(
                    site,
                    enrich_fields,
                    max_pages,
                    browser=browser,
                    credentials=credentials_by_site.get(site.name),
                )
                for row in rows:
                    all_rows.append(merge_field_map(row, enrich_fields))
                # NOTE: max_items is enforced once, below, on the final filtered result —
                # never here. Capping mid-loop meant a single early site filling the quota
                # on its own (easy with multi-page pagination) silently skipped every site
                # listed after it, with no error or warning.
        finally:
            if browser is not None:
                browser.__exit__(None, None, None)

        filtered = apply_filters(all_rows, plan.filters)
        if plan.max_items:
            filtered = filtered[: plan.max_items]
        return filtered

    def _scrape_one(
        self,
        site: SiteSpec,
        fields,
        max_pages: int,
        browser=None,
        credentials: Optional[Tuple[str, str]] = None,
    ) -> List[dict]:
        use_browser = site.method == "browser" or site.login is not None
        if use_browser:
            if browser is None:
                raise RuntimeError(f"Browser required for site '{site.name}' but not available.")
            return browser.scrape_site(site, fields, max_pages, credentials=credentials)
        return self.http.scrape_site(site, fields, max_pages)
