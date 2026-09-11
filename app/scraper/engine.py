from __future__ import annotations

from typing import List, Optional, Tuple

from app.config import settings
from app.models import ScrapePlan, SiteSpec
from app.scraper.extract import apply_filters, merge_field_map
from app.scraper.http_scraper import HttpScraper


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


class ScrapeEngine:
    """Execute a ScrapePlan with HTTP (default) and Playwright when needed."""

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self.http = HttpScraper()
        self.coverage_warnings: List[str] = []

    def run(
        self,
        plan: ScrapePlan,
        credentials_by_site: Optional[dict] = None,
    ) -> List[dict]:
        credentials_by_site = credentials_by_site or {}
        all_rows: List[dict] = []
        self.coverage_warnings = []

        # A plan's method:"browser" only reaches here unverified when it was loaded
        # straight from disk (sidebar "Load"/"Load & run", uploaded JSON) — plans
        # freshly drafted by the AI already get this downgrade in app.ai.planner's
        # parse_plan(). Apply it here too so EVERY entry point is protected, not
        # just the chat path: confirmed a saved plan with method:"browser" crashes
        # ScrapeEngine.run() with a raw Playwright-missing error otherwise.
        if not _playwright_available():
            for site in plan.sites:
                if site.method == "browser" and not site.login:
                    site.method = "http"
                    self.coverage_warnings.append(
                        f"{site.name}: downgraded from browser to http — no login needed "
                        "and Playwright isn't installed (pip install -r requirements-browser.txt "
                        "to enable browser mode)."
                    )

        needs_browser = any(
            site.method == "browser" or site.login is not None for site in plan.sites
        )
        browser = None
        if needs_browser:
            from app.scraper.browser_scraper import BrowserScraper

            browser = BrowserScraper(headless=self.headless)
            browser.__enter__()

        declared_totals: List[Tuple[str, int]] = []
        page_errors: List[str] = []
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
                # Read these right after this site's call — self.http.scrape_site()
                # resets both lists at its own start, so capturing them only once
                # after the whole loop would silently drop every site but the last.
                declared_totals.extend(getattr(self.http, "last_declared_totals", []) or [])
                page_errors.extend(getattr(self.http, "page_errors", []) or [])
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

        for err in page_errors:
            self.coverage_warnings.append(
                f"A page failed after retries and was skipped (results before it are kept): {err}"
            )

        # Surface coverage gaps when a site declared a much larger catalog
        scraped_by_site: dict = {}
        for row in filtered:
            site = str(row.get("_site") or "")
            scraped_by_site[site] = scraped_by_site.get(site, 0) + 1
        for site_name, total in declared_totals:
            got = scraped_by_site.get(site_name, 0)
            if total > 0 and got < total * 0.5:
                self.coverage_warnings.append(
                    f"{site_name}: scraped {got} rows but site reports ~{total} results. "
                    "Pagination may have stopped early, or max_pages/max_items is too low."
                )
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
