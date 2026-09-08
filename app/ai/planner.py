from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from app.ai.client import AIClient, AIError
from app.models import ScrapePlan
from app.scraper.urls import normalize_url

PLANNER_SYSTEM = """You are a scrape-planning assistant for an internal data tool.
Your job is to help the user define WHAT to scrape — not to scrape yourself.

Through conversation, gather:
- goal (what data / products / entities)
- target sites or start URLs (ask if unknown)
- fields to capture (name, price, sku, url, etc.)
- filters / limits (price range, max items, keywords)
- whether any site needs login (only if user says so)
- whether pages are JS-heavy (prefer method "browser") vs static HTML ("http")

When you have enough to draft a plan, reply with a short human summary AND a fenced JSON block:

```json
{ ... ScrapePlan ... }
```

ScrapePlan schema:
{
  "title": "string",
  "goal": "string",
  "fields": [
    {"name": "name", "description": "...", "selector": null, "attribute": null, "required": true},
    {"name": "price", "description": "...", "selector": null, "attribute": null, "required": false},
    {"name": "url", "description": "product link", "selector": null, "attribute": "href", "required": false}
  ],
  "sites": [
    {
      "name": "Example Shop",
      "start_url": "https://www.example-shop.com/products",
      "method": "http",
      "notes": "product listing page",
      "list_selector": null,
      "fields": [],
      "pagination_selector": null,
      "max_pages": 3,
      "login": null,
      "wait_for_selector": null,
      "extra_urls": []
    }
  ],
  "filters": {},
  "max_items": 100,
  "notes": ""
}

Rules:
- Prefer leaving selectors null when unsure — the Python engine uses JSON-LD, Next.js data, meta tags, and heuristics.
- Set method to "browser" only for JS-rendered pages or when login is needed.
- If login is needed, set login to an object with login_url and leave credentials out (user is prompted later).
- NEVER invent or placeholder URLs. Forbidden examples: "https://...", "http://example.com", "klaravik" without domain.
- Always use full real URLs with scheme, e.g. "https://www.klaravik.se/auktion/?q=traktor".
- Do NOT invent URLs the user did not provide or agree to. Ask for URLs when missing.
- Keep plans practical and minimal.
- If the user asks to revise, update the JSON plan accordingly.
"""


def extract_plan_json(text: str) -> Optional[dict]:
    """Pull the last ```json ... ``` block, or a bare JSON object, from model output."""
    fences = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fences:
        raw = fences[-1]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def parse_plan(text: str) -> Optional[ScrapePlan]:
    data = extract_plan_json(text)
    if not data:
        return None
    try:
        plan = ScrapePlan.model_validate(data)
    except Exception:  # noqa: BLE001
        return None
    # Drop / reject placeholder URLs early so scrapes don't blow up on idna errors
    cleaned_sites = []
    for site in plan.sites:
        start = normalize_url(site.start_url)
        if not start:
            continue
        site.start_url = start
        site.extra_urls = [u for u in (normalize_url(x) for x in site.extra_urls) if u]
        if site.login and site.login.login_url:
            login_url = normalize_url(site.login.login_url)
            if login_url:
                site.login.login_url = login_url
            else:
                site.login = None
        cleaned_sites.append(site)
    plan.sites = cleaned_sites
    return plan


class PlannerSession:
    def __init__(self, client: Optional[AIClient] = None) -> None:
        self.client = client or AIClient()
        self.messages: List[Dict[str, str]] = []
        self.latest_plan: Optional[ScrapePlan] = None

    def ask(self, user_text: str) -> Tuple[str, Optional[ScrapePlan]]:
        self.messages.append({"role": "user", "content": user_text})
        try:
            reply = self.client.chat(self.messages, system=PLANNER_SYSTEM)
        except AIError:
            self.messages.pop()
            raise
        self.messages.append({"role": "assistant", "content": reply})
        plan = parse_plan(reply)
        if plan:
            self.latest_plan = plan
        return reply, plan
