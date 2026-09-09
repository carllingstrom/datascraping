"""Site adapters: turn known SPA listing URLs into generic JsonApiSpec configs.

Keep these thin — they only discover HOW to call a JSON API. The scraper itself
stays site-agnostic and paginates via JsonApiSpec.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.models import JsonApiSpec


def maybe_json_api_for_url(start_url: str) -> Optional[JsonApiSpec]:
    host = (urlparse(start_url).hostname or "").lower()
    if host.endswith("gomore.se") or host.endswith("gomore.com") or host.endswith("gomore.dk"):
        return _gomore_api(start_url)
    return None


def _gomore_api(start_url: str) -> Optional[JsonApiSpec]:
    """GoMore car-rental listings are a Vue SPA; data comes from POST .../rental_ads/search."""
    parsed = urlparse(start_url)
    qs = parse_qs(parsed.query)
    place_ref = None
    front_page = qs.get("frontpage", ["false"])[0] == "true" or "frontpage" in (parsed.path + parsed.query)

    raw_place = (qs.get("place") or [None])[0]
    if raw_place:
        try:
            pad = "=" * ((4 - len(raw_place) % 4) % 4)
            place = json.loads(base64.urlsafe_b64decode(raw_place + pad))
            place_ref = place.get("place_ref")
        except Exception:  # noqa: BLE001
            place_ref = None

    if not place_ref:
        # Allow bare /biluthyrning or /cars without place — still searchable nationwide-ish
        place_ref = None

    version = _gomore_api_version(parsed.scheme + "://" + parsed.netloc)
    body = {
        "web_variant": "new",
        "sort_by": "recommended",
        "keyless": False,
        "instant_booking": False,
        "delivery": False,
        "abroad": False,
        "premium": False,
        "pets_welcome": False,
        "dedicated_parking": False,
    }
    if place_ref:
        body["place_ref"] = place_ref
    if front_page:
        body["front_page"] = True

    origin = f"{parsed.scheme}://{parsed.netloc}"
    return JsonApiSpec(
        url=f"{origin}/api/javascript/v{version}/rental_ads/search",
        method="POST",
        body=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": origin,
            "Referer": start_url,
            "X-Requested-With": "XMLHttpRequest",
        },
        items_path="data.hits",
        total_path="data.count",
        page_size=60,
        page_size_key="limit",
        offset_key="offset",
        field_map={
            "model_name": "model",
            "name": "model",
            "product_name": "model",
            "price": "rate",
            "model_year": "car_year",
            "year": "car_year",
            "currency": "currency_code",
            "sku": "id",
            "listing_id": "id",
            "id": "id",
            "location": "formatted_location",
        },
        url_template="https://gomore.se/hyrbil/{id}",
    )


def _gomore_api_version(origin: str) -> int:
    """Probe a small version window; GoMore bumps /api/javascript/vN occasionally."""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": origin,
    }
    # Prefer reading apiVersion from the listing HTML if present
    try:
        html = httpx.get(f"{origin}/biluthyrning", headers={"User-Agent": "Mozilla/5.0"}, timeout=20).text
        m = re.search(r"apiVersion[\"']?\s*[:=]\s*[\"']?(\d+)", html)
        if m:
            return int(m.group(1))
        # translations CDN path often embeds the version
        m = re.search(r"/api/javascript/v(\d+)/", html)
        if m:
            return int(m.group(1))
    except Exception:  # noqa: BLE001
        pass

    for ver in (108, 109, 110, 107, 111, 112):
        try:
            resp = httpx.post(
                f"{origin}/api/javascript/v{ver}/rental_ads/search",
                headers=headers,
                json={"limit": 1, "web_variant": "new", "sort_by": "recommended"},
                timeout=15,
            )
            if resp.status_code == 200:
                return ver
        except Exception:  # noqa: BLE001
            continue
    return 108
