from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from app.ai.client import AIClient, AIError
from app.models import ScrapePlan, SiteSpec
from app.scraper.preview import preview_url
from app.scraper.urls import normalize_url

PLANNER_SYSTEM = """You are a scrape-planning assistant for an internal data tool.
Your job is to help the user define WHAT to scrape — not to scrape yourself.

Through conversation, gather:
- goal (what data / products / entities)
- target sites or start URLs — see the HARD RULE below, this is not optional
- fields to capture (name, price, sku, url, etc.)
- filters / limits (price range, max items, keywords)
- whether any site needs login (only if user says so)
- whether pages are JS-heavy (prefer method "browser") vs static HTML ("http")

HARD RULE — you have no way to browse the web and cannot verify a URL exists or shows the right
content. If the user has not given you an exact URL for a site, your ONLY move is to ask them for
it (or ask them to confirm one you found in earlier conversation). Do NOT guess a plausible-looking
URL for a site you were not given, even one you're confident about — a small automated check WILL
catch a wrong guess, but only after wasting a round; asking first is always faster and correct.
The one exception: if the user explicitly says "you pick the page" / "figure it out yourself",
propose your best guess but say plainly in your reply that it's an unverified guess, not a known
page, so they know to double check it in the preview before running anything.

When the user DID paste literal URL(s): copy them exactly if you can, but don't stress over
getting every character perfect — a tool outside this conversation reads the user's own message
directly and uses those exact URLs verbatim regardless of what you write in the JSON, discarding
whatever you put in "sites" for that site. This is a deliberate, tested decision: retyping a URL
by hand has repeatedly produced subtly wrong, 404ing copies, so your JSON's URLs are a
best-effort courtesy for the human reading your reply, not the thing that actually gets scraped.
Spend your effort instead on fields, method, and notes — those DO come from your JSON as-is.

Once you have ONE correct listing/search page per site, you do NOT need to find more:
- "Scan all pages" / "get everything": the Python engine follows next-page links itself
  (rel="next", a "Next" link, or incrementing a "page=" URL param) and stops automatically once a
  page comes back empty. Just set max_pages generously (e.g. 500) as a safety cap — you never need
  to find or guess a pagination selector.
- Restricting to a category/region/price/etc.: if the single URL the user gave already scopes to
  that (e.g. it's already a "tractors in Germany" page), nothing more to do. If they want several
  such scopes (e.g. multiple countries) and only gave one URL, ask them for the others, or use the
  "filters" block below to narrow the one feed you do have.

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
      "max_pages": 500,
      "login": null,
      "wait_for_selector": null,
      "extra_urls": []
    }
  ],
  "filters": {},
  "max_items": null,
  "notes": ""
}

Rules:
- max_items is a cap on the FINAL combined row count across every site, applied after everything
  else. Leave it null (the default — do not copy the "100" you might have seen in an older
  example) unless the user explicitly asks for a limited sample ("just get me 20", "a quick
  taste"). A low max_items with several sites listed silently cuts off later sites, not just
  trims the total — never set one just because the schema has a slot for it.
- If the user pastes literal URL(s) in their message, use those exact URLs as-is (one site entry
  each) — do not paraphrase, "fix", or substitute a different URL you think is better. A tool
  outside this conversation double-checks this automatically, so getting it letter-perfect isn't
  on you, but starting from the user's own URLs is always the right move when they gave you one.
- Prefer leaving selectors null when unsure — the Python engine uses JSON-LD, Next.js embedded data,
  meta tags, and listing-card heuristics to extract rows automatically.
- start_url MUST be an actual listing/search-results page (the page that already shows multiple
  items), never a homepage or category landing page — those usually render only one "item" (the
  page itself) when scraped. If the URL came from the user, trust it as given. If you had to guess
  it (see HARD RULE above — should be rare), say so.
- Set method to "browser" only for JS-rendered pages or when login is needed.
- If login is needed, set login to an object with login_url and leave credentials out (user is prompted later).
- NEVER invent or placeholder URLs. Forbidden examples: "https://...", "http://example.com", "klaravik" without domain.
- Always use full real URLs with scheme, e.g. "https://www.klaravik.se/auktion/?q=traktor".
- Do NOT invent URLs the user did not provide or agree to. Ask for URLs when missing.
- Keep plans practical and minimal.
- If the user asks to revise, update the JSON plan accordingly.

filters schema (the ONLY keys the engine actually reads — anything else is silently ignored):
{
  "keywords": ["optional", "substring", "matches", "across", "any", "field"],
  "min_price": 2000,
  "max_price": null,
  "field_in": {"country": ["DE", "FR", "SE"], "category": ["tractors"]}
}
"field_in" does a case-insensitive substring match against whatever that row field actually
contains — only useful once you know (from a preview) what values that field holds.

AUTOMATED PREVIEW: after you emit a plan, the tool actually fetches each site's start_url and
runs the real extraction engine on it — you do NOT do this yourself, and you have no other way to
see the live page. You'll get a message starting "[SYSTEM: automated preview check ...]" reporting
how many rows were found and via which method. If it found 0-1 rows, your start_url is almost
certainly wrong (homepage instead of listing page) — propose a corrected start_url or selectors
and re-emit the full JSON plan. If a field you were asked for isn't showing up in the sample, say
so plainly rather than guessing — some fields (e.g. a private-vs-dealer seller flag) may simply not
be exposed by a given site's data, and that should be told to the user as a gap, not silently
invented.
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


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def parse_plan(text: str) -> Optional[ScrapePlan]:
    data = extract_plan_json(text)
    if not data:
        return None
    try:
        plan = ScrapePlan.model_validate(data)
    except Exception:  # noqa: BLE001
        return None
    have_playwright = _playwright_available()
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
        # The model defaults to "browser" surprisingly often even for plain static pages.
        # Playwright isn't installed by default (see README) — rather than crash on a
        # missing-module error at run time, downgrade to "http" when there's no login
        # requirement, since that's the case the plain HTTP engine already handles.
        if site.method == "browser" and not site.login and not have_playwright:
            site.method = "http"
            site.notes = (site.notes + " " if site.notes else "") + (
                "[auto-downgraded from browser to http: no login needed and Playwright "
                "isn't installed — see requirements-browser.txt to enable browser mode]"
            )
        cleaned_sites.append(site)
    plan.sites = cleaned_sites
    return plan


_URL_IN_TEXT_RE = re.compile(r'https?://\S+', re.IGNORECASE)


def extract_urls_from_text(text: str) -> List[str]:
    """Pull literal URLs the user typed/pasted, in order, deduped.

    Interior commas are NOT a URL boundary — real URLs (e.g. Mascus's own
    "/tractors/de,country.html") use them mid-path. But a comma is always stripped when it's
    the very LAST character of the match: a URL never legitimately ends on a bare comma, and
    a comma there is always a list separator from "url1, url2, url3" — leaving it in silently
    corrupts the URL (confirmed: this exact bug turned 8 of 9 pasted URLs into 404s/wrong pages,
    while the one URL with nothing after it — no trailing comma — worked correctly).
    """
    found: List[str] = []
    for m in _URL_IN_TEXT_RE.finditer(text or ""):
        raw = m.group(0)
        while raw and raw[-1] in ".,)]":
            if raw[-1] == ")" and raw.count("(") >= raw.count(")"):
                break
            if raw[-1] == "]" and raw.count("[") >= raw.count("]"):
                break
            raw = raw[:-1]
        clean = normalize_url(raw)
        if clean and clean not in found:
            found.append(clean)
    return found


_CATALOG_KEY_RE = re.compile(r"catalogs?=([A-Za-z0-9_-]+)", re.IGNORECASE)
_SLUG_KEY_RE = re.compile(r"categor(?:y|ies)=([A-Za-z0-9_-]+)", re.IGNORECASE)
_GENERIC_PAGE_SEGMENT_RE = re.compile(r"^\d+,[\w-]+$")


def _slug_for_url(url: str, index: int) -> str:
    """A short, human-distinguishing label for a URL — prefers a "categories=X" style
    query token (common on listing sites) over the raw last path segment, since sites like
    Mascus put the SAME generic segment (e.g. "1,relevance,search.html") on every listing
    URL regardless of category, which otherwise made every site in a plan look identically
    named in the preview table.
    """
    m = _SLUG_KEY_RE.search(url)
    if m:
        return m.group(1)
    path_seg = urlparse(url).path.strip("/").split("/")[-1]
    if path_seg and not _GENERIC_PAGE_SEGMENT_RE.match(path_seg):
        return path_seg
    return f"page-{index + 1}"


def _label_for_url(url: str, index: int, fallback_prefix: str) -> str:
    """Build each injected site's display name from ITS OWN url, not a name borrowed from
    whichever site the model happened to list first — confirmed bug: every one of 9 distinct
    category URLs (forestry/construction/agriculture) showed up labeled "Forestry — ..." in
    the results summary purely because the model's first site entry was named "Forestry",
    even though the actual scraped data was correctly separated by category all along.
    """
    catalog_m = _CATALOG_KEY_RE.search(url)
    slug = _slug_for_url(url, index)
    if catalog_m and catalog_m.group(1) != slug:
        return f"{catalog_m.group(1)}: {slug}"
    if catalog_m:
        return catalog_m.group(1)
    return f"{fallback_prefix} — {slug}"


def ensure_user_urls(plan: ScrapePlan, urls: List[str]) -> ScrapePlan:
    """When the user has typed literal URL(s) into the chat, those and ONLY those get
    scraped — replacing whatever the model put in "sites" outright, not merely supplementing
    it. A local model has repeatedly proven unreliable even at transcribing a URL verbatim
    (confirmed: it retyped 9 correct URLs into 9 different ones, all 404s), so once real
    URLs exist there is no reason to keep the model's own guesses around at all — every
    field/method/pagination setting it chose is preserved as a template, just not its URLs.
    """
    if not urls:
        return plan
    template = plan.sites[0] if plan.sites else None
    new_sites = []
    for i, u in enumerate(urls):
        name = _label_for_url(u, i, template.name if template else "Mascus")
        new_sites.append(
            SiteSpec(
                name=name[:60],
                start_url=u,
                method=template.method if template else "http",
                fields=list(template.fields) if template else [],
                max_pages=template.max_pages if template else None,
                pagination_selector=template.pagination_selector if template else None,
                list_selector=template.list_selector if template else None,
            )
        )
    plan.sites = new_sites
    return plan


def preview_plan(plan: ScrapePlan) -> List[Tuple[SiteSpec, dict]]:
    """Fetch each site's start_url once and report what the extraction engine finds there."""
    results = []
    for site in plan.sites:
        fields = plan.resolved_fields_for(site)
        result = preview_url(site.start_url, site.list_selector, fields)
        results.append((site, result))
    return results


def plan_is_weak(previews: List[Tuple[SiteSpec, dict]]) -> bool:
    return any((not result["ok"]) or result["count"] <= 1 for _, result in previews)


def build_preview_feedback(
    previews: List[Tuple[SiteSpec, dict]], user_supplied_urls: Optional[List[str]] = None
) -> str:
    user_supplied_urls = user_supplied_urls or []
    lines = [
        "[SYSTEM: automated preview check — this fetched each site's start_url for real and ran "
        "the extraction engine on it, exactly as the full scrape would on page 1]"
    ]
    any_weak_guessed = False
    for site, result in previews:
        from_user = site.start_url in user_supplied_urls
        tag = "user-supplied" if from_user else "AI-GUESSED"
        if not result["ok"]:
            lines.append(f"- {site.name} [{tag}] ({site.start_url}): FAILED to fetch — {result['error']}")
            if not from_user:
                any_weak_guessed = True
            continue
        detail = (
            f"- {site.name} [{tag}] ({site.start_url}): found {result['count']} row(s) via "
            f"'{result['source']}' extraction"
        )
        if result["sample"]:
            detail += f"; sample fields present: {sorted(result['sample'][0].keys())}"
        if result["count"] <= 1:
            detail += " — this looks wrong for a listing page."
            if not from_user:
                any_weak_guessed = True
        lines.append(detail)
    if any_weak_guessed:
        lines.append(
            "\nOne or more [AI-GUESSED] URLs above found 0-1 rows. Per the HARD RULE: stop "
            "guessing further URLs for that site — ask the user for the exact listing/search page "
            "instead, in your reply. Do not re-emit a JSON plan with another guessed URL."
        )
    else:
        lines.append(
            "\nFor any [user-supplied] site with 0-1 rows: that's a real fetch problem (blocked, "
            "wrong page type, needs JS) — mention it plainly to the user rather than silently "
            "retrying with a different URL they didn't give you. Sites that already found multiple "
            "rows are working — leave them as-is. Also check the sample fields against what the "
            "user asked for — if something they wanted isn't among the sample fields, say so "
            "plainly rather than guessing at where it might be."
        )
    return "\n".join(lines)


_MAX_DISPATCHED_MESSAGES = 12  # ~6 exchanges — bounds prompt size on slow local CPU inference


class PlannerSession:
    def __init__(self, client: Optional[AIClient] = None) -> None:
        self.client = client or AIClient()
        self.messages: List[Dict[str, str]] = []
        self.latest_plan: Optional[ScrapePlan] = None
        self.user_supplied_urls: List[str] = []

    def _dispatch_messages(self) -> List[Dict[str, str]]:
        """Full history is kept in self.messages, but only a bounded tail is actually sent —
        each turn otherwise gets slower than the last as a session grows, which compounds badly
        on CPU-only local inference. If anything got trimmed, prepend a compact recap of the plan
        so the model doesn't lose track of what's already been established.
        """
        trimmed = self.messages[-_MAX_DISPATCHED_MESSAGES:]
        if len(trimmed) == len(self.messages) or not self.latest_plan:
            return trimmed
        plan = self.latest_plan
        recap = {
            "role": "user",
            "content": (
                "[CONTEXT RECAP — earlier turns were trimmed to keep this response fast; this "
                f"summarizes them, not a new instruction] Plan so far: title={plan.title!r}, "
                f"goal={plan.goal!r}, sites={[s.start_url for s in plan.sites]}, "
                f"fields={[f.name for f in plan.fields]}, filters={plan.filters!r}."
            ),
        }
        return [recap] + trimmed

    def refine_with_preview(
        self, plan: ScrapePlan, max_rounds: int = 2
    ) -> Tuple[ScrapePlan, List[Tuple[SiteSpec, dict]], List[str]]:
        """Auto-loop: preview the plan's URL(s); if extraction looks weak, hand the AI real
        feedback from the live page and let it correct itself — without bothering the user.
        Returns (final_plan, final_previews, extra_assistant_replies_shown_along_the_way).
        """
        previews = preview_plan(plan)
        transcript: List[str] = []
        rounds = 0
        while plan_is_weak(previews) and plan.sites and rounds < max_rounds:
            feedback = build_preview_feedback(previews, self.user_supplied_urls)
            try:
                reply, new_plan = self.ask(feedback)
            except AIError:
                break
            transcript.append(reply)
            if not new_plan:
                break
            plan = new_plan
            previews = preview_plan(plan)
            rounds += 1
        return plan, previews, transcript

    def ask(self, user_text: str) -> Tuple[str, Optional[ScrapePlan]]:
        self.messages.append({"role": "user", "content": user_text})
        for u in extract_urls_from_text(user_text):
            if u not in self.user_supplied_urls:
                self.user_supplied_urls.append(u)
        try:
            reply = self.client.chat(self._dispatch_messages(), system=PLANNER_SYSTEM)
        except AIError:
            self.messages.pop()
            raise
        self.messages.append({"role": "assistant", "content": reply})
        plan = parse_plan(reply)
        if plan:
            # Any URL the user actually typed is scraped verbatim, whether or not the
            # model transcribed it correctly into the JSON it emitted.
            plan = ensure_user_urls(plan, self.user_supplied_urls)
            self.latest_plan = plan
        return reply, plan
