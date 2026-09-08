from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models import FieldSpec
from app.scraper.extract import extract_page_rows
from app.scraper.http_scraper import HttpScraper
from app.scraper.urls import guess_next_page, normalize_url


def preview_url(
    url: str,
    list_selector: Optional[str] = None,
    fields: Optional[List[FieldSpec]] = None,
) -> Dict[str, Any]:
    """Fetch a single page and report what the extraction engine actually finds there.

    One HTTP request, no pagination — lets a drafted plan be sanity-checked against
    the live page before committing to a full multi-page scrape.
    """
    fields = fields or []
    clean = normalize_url(url)
    if not clean:
        return {"ok": False, "url": url, "error": f"invalid or placeholder URL: {url!r}"}

    scraper = HttpScraper()
    try:
        soup = scraper.fetch(clean)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": clean, "error": str(exc)}

    rows, source = extract_page_rows(soup, clean, list_selector, fields)
    next_page = guess_next_page(clean, soup)

    sample = []
    for row in rows[:2]:
        sample.append({k: v for k, v in row.items() if not str(k).startswith("_")})

    return {
        "ok": True,
        "url": clean,
        "source": source,
        "count": len(rows),
        "sample": sample,
        "next_page_url": next_page,
        "has_pagination_hint": bool(next_page),
    }
