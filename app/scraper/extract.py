from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models import FieldSpec


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _attr_or_text(el, attribute: Optional[str]) -> str:
    if el is None:
        return ""
    if attribute:
        return _clean(el.get(attribute) or "")
    return _clean(el.get_text(" ", strip=True))


def parse_json_ld_products(soup: BeautifulSoup, page_url: str) -> List[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _walk_ld(data):
            types = node.get("@type")
            type_list = types if isinstance(types, list) else [types]
            type_list = [str(t).lower() for t in type_list if t]
            if "product" not in type_list:
                continue
            offer = node.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            price = ""
            currency = ""
            if isinstance(offer, dict):
                price = str(offer.get("price") or offer.get("lowPrice") or "")
                currency = str(offer.get("priceCurrency") or "")
            link = node.get("url") or page_url
            if isinstance(link, str) and not link.startswith(("http://", "https://")):
                link = urljoin(page_url, link)
            products.append(
                {
                    "name": _clean(str(node.get("name") or "")),
                    "product_name": _clean(str(node.get("name") or "")),
                    "price": _clean(f"{price} {currency}".strip()),
                    "sku": _clean(str(node.get("sku") or node.get("mpn") or "")),
                    "brand": _clean(
                        str(
                            (node.get("brand") or {}).get("name")
                            if isinstance(node.get("brand"), dict)
                            else node.get("brand") or ""
                        )
                    ),
                    "description": _clean(str(node.get("description") or "")),
                    "url": _clean(str(link or "")),
                    "image": _clean(
                        str(
                            node.get("image")[0]
                            if isinstance(node.get("image"), list) and node.get("image")
                            else node.get("image") or ""
                        )
                    ),
                    "_source": "json-ld",
                }
            )
    return products


def _walk_ld(data: Any) -> List[dict]:
    out: List[dict] = []
    if isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            for item in data["@graph"]:
                out.extend(_walk_ld(item))
        else:
            out.append(data)
    elif isinstance(data, list):
        for item in data:
            out.extend(_walk_ld(item))
    return out


def meta_fallback(soup: BeautifulSoup, page_url: str) -> Dict[str, Any]:
    def meta(*keys: str) -> str:
        for key in keys:
            tag = soup.find("meta", property=key) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                return _clean(tag["content"])
        return ""

    title = meta("og:title", "twitter:title") or _clean(
        soup.title.get_text() if soup.title else ""
    )
    description = meta("og:description", "description")
    year = _year_from_text(description) or _year_from_text(title)
    return {
        "name": title,
        "product_name": title,
        "price": meta("product:price:amount", "og:price:amount"),
        "description": description,
        "model_year": year,
        "url": meta("og:url") or page_url,
        "image": meta("og:image"),
        "_source": "meta",
    }


def extract_with_selectors(
    soup: BeautifulSoup,
    page_url: str,
    list_selector: Optional[str],
    fields: List[FieldSpec],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if list_selector:
        items = soup.select(list_selector)
    else:
        items = [soup]

    for item in items:
        row: Dict[str, Any] = {"_source": "selector", "url": page_url}
        for field in fields:
            if not field.selector:
                continue
            el = item.select_one(field.selector)
            value = _attr_or_text(el, field.attribute)
            if field.attribute in {"href", "src"} and value and not value.startswith(
                ("http://", "https://", "data:", "mailto:", "#")
            ):
                value = urljoin(page_url, value)
            row[field.name] = value
        if any(v for k, v in row.items() if not k.startswith("_") and v):
            rows.append(row)
    return rows


def heuristic_product_cards(soup: BeautifulSoup, page_url: str) -> List[Dict[str, Any]]:
    """Best-effort extraction for common listing patterns when no selectors exist."""
    rows: List[Dict[str, Any]] = []
    candidates = soup.select(
        "article.product_card, [class*='product'], [class*='item'], [data-product], article, .card"
    )
    seen = set()
    for card in candidates[:2000]:
        link = card.find("a", href=True)
        if not link:
            continue
        href = urljoin(page_url, link["href"])
        name = _clean(link.get("title") or "") or _clean(link.get_text(" ", strip=True))
        if not name:
            heading = card.find(["h1", "h2", "h3", "h4", "p"])
            name = _clean(heading.get_text(" ", strip=True) if heading else "")
        # Klaravik title class
        title_el = card.select_one(".product_card__title, [class*='title']")
        if title_el:
            name = _clean(title_el.get_text(" ", strip=True)) or name
        if not name or href in seen:
            continue
        price = ""
        bid = card.select_one(".product_card__current-bid, [class*='price'], [class*='bid']")
        if bid:
            price = _clean(bid.get_text(" ", strip=True))
        if not price:
            price_el = card.find(string=re.compile(r"(\$|€|£|\bSEK\b|\bUSD\b|\bEUR\b)\s?\d", re.I))
            if price_el:
                price = _clean(str(price_el))
        seen.add(href)
        rows.append(
            {
                "name": name,
                "product_name": name,
                "price": price,
                "url": href,
                "model_year": _year_from_text(card.get_text(" ", strip=True)),
                "_source": "heuristic",
            }
        )
    return rows


def extract_next_data_listings(soup: BeautifulSoup, page_url: str) -> List[Dict[str, Any]]:
    """Pull listing arrays from Next.js __NEXT_DATA__ (e.g. Mascus)."""
    tag = soup.select_one("script#__NEXT_DATA__")
    if not tag or not tag.string:
        return []
    try:
        data = json.loads(tag.string)
    except json.JSONDecodeError:
        return []

    items = _find_listing_arrays(data)
    rows: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        brand = item.get("brand") or ""
        model = item.get("model") or ""
        name = _clean(f"{brand} {model}".strip()) or _clean(
            str(item.get("title") or item.get("name") or "")
        )
        year = item.get("yearOfManufacture") or item.get("year") or item.get("modelYear")
        price_val = (
            item.get("priceOriginal")
            or item.get("priceInUserCurrency")
            or item.get("priceEURO")
            or item.get("price")
        )
        currency = item.get("priceOriginalUnit") or item.get("userCurrency") or ""
        price = _clean(f"{price_val} {currency}".strip()) if price_val not in (None, "") else ""
        href = item.get("assetUrl") or item.get("url") or item.get("link") or ""
        if href and not str(href).startswith(("http://", "https://")):
            href = urljoin(page_url, str(href))
        if not name and not href:
            continue
        # Curated, human-friendly names for the fields this shape usually carries...
        row: Dict[str, Any] = {
            "name": name,
            "product_name": name,
            "brand": _clean(str(brand)),
            "model": _clean(str(model)),
            "price": price,
            "currency": _clean(str(currency)),
            "model_year": str(year) if year not in (None, "") else "",
            "hours": str(item.get("meterReadout") or ""),
            "location": _clean(str(item.get("locationCity") or "")),
            "country": _clean(str(item.get("locationCountryCode") or "")),
            "category": _clean(str(item.get("categoryName") or item.get("catalogName") or "")),
            "listing_date": _clean(str(item.get("createDate") or "")),
            "listing_id": _clean(
                str(item.get("productId") or item.get("rbListingID") or item.get("sku") or "")
            ),
            "seller_name": _clean(str(item.get("companyName") or "")),
            "sku": _clean(str(item.get("productId") or item.get("sku") or "")),
            "url": _clean(str(href)),
            "_source": "next-data",
        }
        # ...plus every raw key verbatim, so a plan field that names the site's own
        # JSON key directly (spotted via preview) still comes through untouched.
        for key, value in item.items():
            row.setdefault(key, value)
        rows.append(row)
    return rows


def _find_listing_arrays(data: Any) -> List[dict]:
    found: List[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    key in {"items", "results", "listings", "ads", "products"}
                    and isinstance(value, list)
                    and value
                    and isinstance(value[0], dict)
                ):
                    sample = value[0]
                    keys = set(sample.keys())
                    if keys & {
                        "yearOfManufacture",
                        "priceOriginal",
                        "assetUrl",
                        "brand",
                        "model",
                        "price",
                        "title",
                    }:
                        found.extend(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node[:50]:
                walk(item)

    walk(data)
    return found


def _year_from_text(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"(?:Fordonsår[^0-9]{0,40}|Årsmodell[^0-9]{0,20}|Modellår[^0-9]{0,20}|Tillverkningsår[^0-9]{0,20}|Year[^0-9]{0,20})((?:19|20)\d{2})",
        r"\b((?:19|20)\d{2})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            year = m.group(1)
            # avoid matching random years like auction dates far in future? allow 1950-2035
            try:
                y = int(year)
                if 1950 <= y <= 2035:
                    return year
            except ValueError:
                continue
    return ""


def apply_filters(rows: List[Dict[str, Any]], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not filters:
        return rows
    keywords = filters.get("keywords") or filters.get("contains") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [str(k).lower() for k in keywords if k]

    # Accept both the flat shape (min_price/max_price) and a nested {"price": {"min": .., "max": ..}}
    # shape models sometimes emit — support both so a filter never silently gets dropped.
    min_price = filters.get("min_price")
    max_price = filters.get("max_price")
    price_block = filters.get("price")
    if isinstance(price_block, dict):
        min_price = min_price if min_price is not None else price_block.get("min")
        max_price = max_price if max_price is not None else price_block.get("max")

    # field_in: {"field_name": ["value1", "value2", ...]} — case-insensitive substring match
    # against that row field. Lets a plan restrict to e.g. specific countries/categories
    # when that can't be expressed via the site's own URL.
    field_in: Dict[str, List[str]] = {}
    for key, values in (filters.get("field_in") or {}).items():
        if isinstance(values, str):
            values = [values]
        field_in[str(key)] = [str(v).lower() for v in values if v]

    out: List[Dict[str, Any]] = []
    for row in rows:
        blob = " ".join(str(v) for k, v in row.items() if not str(k).startswith("_")).lower()
        if keywords and not any(k in blob for k in keywords):
            continue
        price_num = _parse_price(row.get("price"))
        if min_price is not None and price_num is not None and price_num < float(min_price):
            continue
        if max_price is not None and price_num is not None and price_num > float(max_price):
            continue
        if field_in:
            row_lower = {str(k).lower(): str(v).lower() for k, v in row.items()}
            skip = False
            for field_name, allowed in field_in.items():
                value = row_lower.get(field_name.lower(), "")
                if not any(a in value for a in allowed):
                    skip = True
                    break
            if skip:
                continue
        out.append(row)
    return out


def _parse_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value)
    match = re.search(r"(\d+[.,]?\d*)", text.replace(" ", "").replace("\xa0", ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def extract_page_rows(
    soup: BeautifulSoup,
    page_url: str,
    list_selector: Optional[str],
    fields: List[FieldSpec],
) -> Tuple[List[Dict[str, Any]], str]:
    """Run the full extraction cascade once. Returns (rows, source_used).

    Shared by the real scraper and the preview tool so a plan can be sanity-checked
    against the live page before committing to a multi-page run.
    """
    if list_selector or any(f.selector for f in fields):
        rows = extract_with_selectors(soup, page_url, list_selector, fields)
        if rows:
            return rows, "selectors"
    rows = extract_next_data_listings(soup, page_url)
    if rows:
        return rows, "next-data"
    rows = parse_json_ld_products(soup, page_url)
    if rows:
        return rows, "json-ld"
    rows = heuristic_product_cards(soup, page_url)
    if rows:
        return rows, "heuristic"
    meta = meta_fallback(soup, page_url)
    if meta.get("name"):
        return [meta], "meta"
    return [], "none"


# Common alternate names a site's own data might use for a field the plan asked for.
# Matched case-insensitively against whatever raw keys extract_next_data_listings /
# parse_json_ld_products left on the row (see extract_next_data_listings's key passthrough).
_FIELD_SYNONYMS: Dict[str, List[str]] = {
    "model_name": ["model", "name", "product_name", "title"],
    "model_year": ["yearofmanufacture", "modelyear", "year", "car_year"],
    "hours": ["meterreadout", "odometer", "mileage"],
    "country": ["locationcountrycode", "country", "companycountry"],
    "category": ["categoryname", "catalogname", "category"],
    "listing_date": ["createdate", "listingdate", "datepublished", "datePosted"],
    "listing_id": ["productid", "rblistingid", "listingid", "sku", "id"],
    "make_model": ["brand_model", "brandmodel"],
    "price": ["priceoriginal", "priceinusercurrency", "price", "rate", "average_daily_rate"],
    "currency": ["priceoriginalunit", "usercurrency", "currency", "currency_code"],
    "seller": ["companyname", "seller_name", "sellername"],
}


def merge_field_map(row: Dict[str, Any], fields: List[FieldSpec]) -> Dict[str, Any]:
    """Ensure planned field names exist on the row, filling from known synonyms first."""
    out = dict(row)
    lower_keys = {str(k).lower(): k for k in row.keys()}
    # common aliases
    if "product_name" in {f.name for f in fields} and not out.get("product_name"):
        out["product_name"] = out.get("name", "")
    if "name" in {f.name for f in fields} and not out.get("name"):
        out["name"] = out.get("product_name", "")
    if "listings" in {f.name for f in fields}:
        out.setdefault("listings", "")
    for field in fields:
        if out.get(field.name):
            continue
        for synonym in _FIELD_SYNONYMS.get(field.name.lower(), []):
            source_key = lower_keys.get(synonym.lower())
            if source_key and row.get(source_key) not in (None, ""):
                out[field.name] = row[source_key]
                break
        out.setdefault(field.name, out.get(field.name, ""))
    return out
