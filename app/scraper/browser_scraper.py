from __future__ import annotations

from typing import List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models import FieldSpec, LoginSpec, SiteSpec
from app.scraper.auth import maybe_env_credentials, prompt_credentials
from app.scraper.extract import extract_page_rows
from app.scraper.urls import guess_next_page, normalize_url


class BrowserScraper:
    """Playwright-backed scraper for JS pages and optional login."""

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "BrowserScraper":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for browser/login scrapes. "
                "Run: pip install playwright && playwright install chromium"
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def login(self, site_name: str, login: LoginSpec, credentials: Optional[Tuple[str, str]] = None) -> None:
        assert self._context is not None
        creds = credentials or maybe_env_credentials(login)
        if not creds:
            creds = prompt_credentials(site_name, login)
        username, password = creds
        page = self._context.new_page()
        page.goto(login.login_url, wait_until="domcontentloaded")
        page.fill(login.username_selector, username)
        page.fill(login.password_selector, password)
        page.click(login.submit_selector)
        page.wait_for_load_state("networkidle")
        page.close()

    def scrape_site(
        self,
        site: SiteSpec,
        fields: List[FieldSpec],
        max_pages: int,
        credentials: Optional[Tuple[str, str]] = None,
    ) -> List[dict]:
        assert self._context is not None
        if site.login:
            self.login(site.name, site.login, credentials=credentials)

        urls = [site.start_url] + list(site.extra_urls)
        collected: List[dict] = []
        visited = set()

        for start in urls:
            page_url: Optional[str] = normalize_url(start) or start
            for _ in range(max_pages):
                if not page_url or page_url in visited:
                    break
                visited.add(page_url)
                page = self._context.new_page()
                page.goto(page_url, wait_until="domcontentloaded")
                if site.wait_for_selector:
                    page.wait_for_selector(site.wait_for_selector, timeout=15000)
                else:
                    page.wait_for_timeout(800)
                html = page.content()
                soup = BeautifulSoup(html, "lxml")
                rows, _source = extract_page_rows(soup, page_url, site.list_selector, fields)
                for row in rows:
                    row.setdefault("_site", site.name)
                    row.setdefault("_page", page_url)
                collected.extend(rows)

                next_url = None
                if not rows:
                    page.close()
                    break
                if site.pagination_selector:
                    nxt = page.query_selector(site.pagination_selector)
                    if nxt:
                        href = nxt.get_attribute("href")
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
                page.close()
                page_url = next_url

        return collected
