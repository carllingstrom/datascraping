"""Generic JSON-API listing scraper (offset/limit pagination)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.models import FieldSpec, JsonApiSpec


def _dig(data: Any, path: Optional[str]) -> Any:
    if not path:
        return None
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _map_item(
    item: Dict[str, Any],
    field_map: Dict[str, str],
    fields: List[FieldSpec],
    site_url: str,
    url_template: Optional[str] = None,
) -> Dict[str, Any]:
    """Map one API hit into plan fields only (plus url / meta)."""
    wanted = {f.name for f in fields}
    # Always keep a listing URL when we can build one
    wanted.add("url")

    row: Dict[str, Any] = {"_source": "json-api"}

    # Apply explicit field_map, but only for columns the plan asked for
    for dest, src in field_map.items():
        if dest not in wanted and dest not in {"currency", "currency_code"}:
            continue
        val = item.get(src)
        if val in (None, ""):
            continue
        row[dest] = val

    if url_template and not row.get("url"):
        try:
            row["url"] = url_template.format(**item)
        except Exception:  # noqa: BLE001
            pass
    elif not row.get("url") and item.get("id") and "gomore." in site_url:
        row["url"] = f"https://gomore.se/hyrbil/{item['id']}"

    # Compose price with currency when both exist
    currency = row.get("currency") or row.get("currency_code") or item.get("currency_code") or ""
    if "price" in wanted:
        if row.get("price") not in (None, "") and currency and " " not in str(row["price"]):
            row["price"] = f"{row['price']} {currency}"
        elif row.get("price") in (None, "") and item.get("rate") not in (None, ""):
            row["price"] = f"{item['rate']} {currency}".strip()

    # Drop helper keys that were only used for formatting
    row.pop("currency", None)
    row.pop("currency_code", None)

    for f in fields:
        row.setdefault(f.name, "")
    return row

def scrape_json_api(
    api: JsonApiSpec,
    fields: List[FieldSpec],
    max_pages: int,
    site_name: str,
    referer_url: str,
) -> List[dict]:
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/json",
        **(api.headers or {}),
    }
    collected: List[dict] = []
    declared_total: Optional[int] = None

    with httpx.Client(timeout=settings.request_timeout, follow_redirects=True, headers=headers) as client:
        for page_idx in range(max_pages):
            payload = dict(api.body or {})
            if api.page_size_key:
                payload[api.page_size_key] = api.page_size
            offset = api.page_size * page_idx
            if offset > 0:
                payload[api.offset_key] = offset
            elif api.offset_key in payload and page_idx == 0:
                payload.pop(api.offset_key, None)

            if api.method.upper() == "GET":
                resp = client.get(api.url, params=payload)
            else:
                resp = client.post(api.url, json=payload)
            resp.raise_for_status()
            data = resp.json()

            if page_idx == 0 and api.total_path:
                total = _dig(data, api.total_path)
                if isinstance(total, int):
                    declared_total = total

            items = _dig(data, api.items_path) or []
            if not isinstance(items, list) or not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                # Skip promo/non-listing cards when a type discriminator exists
                item_type = item.get("type")
                if item_type and item_type not in {"result", "rental_ad", "listing", "product", "ad"}:
                    continue
                row = _map_item(
                    item,
                    api.field_map,
                    fields,
                    referer_url,
                    url_template=api.url_template,
                )
                row.setdefault("_site", site_name)
                row.setdefault("_page", f"{api.url}#offset={offset}")
                collected.append(row)

            if len(items) < api.page_size:
                break
            if declared_total is not None and len(collected) >= declared_total:
                break

    # Stash total for coverage warnings (engine reads via attribute if present)
    scrape_json_api.last_declared_total = declared_total  # type: ignore[attr-defined]
    return collected
