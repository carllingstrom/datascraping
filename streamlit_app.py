"""Streamlit UI for DataScraper — chat to plan, run scrape, download Excel.

Local: Ollama / Gemini / Groq / Claude.
Streamlit Cloud: Gemini or Groq (free API keys via Secrets).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _apply_streamlit_secrets() -> None:
    """Map st.secrets into env vars before settings are loaded.

    Only credentials + operational knobs — never model IDs. Streamlit Cloud
    Secrets often keep stale GEMINI_MODEL values that override code defaults
    and break chat after Gemini renames models.
    """
    try:
        secrets = st.secrets
    except Exception:  # noqa: BLE001
        return
    keys = (
        "AI_PROVIDER",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "OLLAMA_BASE_URL",
        "MAX_PAGES_DEFAULT",
        "ENRICH_DETAIL_LIMIT",
        "REQUEST_TIMEOUT",
        "USER_AGENT",
    )
    for key in keys:
        try:
            value = secrets[key]
        except Exception:  # noqa: BLE001
            continue
        if value is not None and str(value).strip() != "":
            os.environ[key] = str(value).strip()

    # Drop any model pins that may already be in the process env from Secrets
    # or a leftover local .streamlit/secrets.toml so code defaults always win.
    for stale in (
        "GEMINI_MODEL",
        "GROQ_MODEL",
        "ANTHROPIC_MODEL",
        "OLLAMA_MODEL",
    ):
        os.environ.pop(stale, None)


def _running_on_streamlit_cloud() -> bool:
    """Best-effort detect Streamlit Community Cloud."""
    if os.getenv("STREAMLIT_SHARING_MODE") or os.getenv("STREAMLIT_RUNTIME_ENV"):
        return True
    hostname = (os.getenv("HOSTNAME") or "").lower()
    if "streamlit" in hostname or hostname.endswith(".streamlit.app"):
        return True
    try:
        if "/mount/src/" in Path.cwd().as_posix():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


_apply_streamlit_secrets()
if _running_on_streamlit_cloud():
    os.environ.setdefault("AI_PROVIDER", "gemini")

from app.ai.client import AIClient, AIError  # noqa: E402
from app.ai.planner import PlannerSession  # noqa: E402
from app.cli import load_plan, save_plan  # noqa: E402
from app.config import reload_settings  # noqa: E402
from app.export.excel import rows_to_excel  # noqa: E402
from app.models import ScrapePlan  # noqa: E402
from app.scraper.engine import ScrapeEngine  # noqa: E402
from app.scraper.preview import preview_url  # noqa: E402

settings = reload_settings()

st.set_page_config(
    page_title="DataScraper",
    page_icon=None,
    layout="wide",
    # Without this, Streamlit auto-collapses the sidebar on narrower viewports
    # (roughly <768px) with only a small, easy-to-miss ">>" arrow to reopen it —
    # which looks exactly like "nothing in the sidebar happened". Force it open.
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --ds-ink: #1c1f26;
        --ds-muted: #5b6472;
        --ds-line: #dfe2e7;
        --ds-accent: #2f4858;
        --ds-bg-soft: #f6f7f9;
    }
    /* font-family is inherited — setting it on body alone reaches every normal text
       element without the blanket [class*="css"] selector this used to have. That
       selector matched icon-font spans too (Streamlit's sidebar expand/collapse arrows
       are a ligature-text icon font, e.g. "keyboard_double_arrow_right" rendered via a
       specific font-family) and overrode their font, so the ligature text rendered as
       literal oversized text instead of the arrow glyph — invisible-looking in a tiny
       icon button. Confirmed via computed style before this fix. */
    html, body {
        font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    /* NOT hiding header[data-testid="stHeader"] itself — the sidebar's own reopen
       control, [data-testid="stExpandSidebarButton"], lives inside that header, and
       visibility:hidden is inherited: hiding the header made the reopen arrow
       unreachable (invisible AND unclickable) whenever the sidebar was collapsed,
       with no way to bring it back. Confirmed via the live DOM (ancestor chain walk)
       that this exact rule was the cause. Hide only the specific decorative buttons
       inside the header instead. */
    footer, [data-testid="stAppDeployButton"], [data-testid="stMainMenu"] { visibility: hidden; }
    div.block-container { padding-top: 2.2rem; max-width: 1100px; }
    h1 { font-weight: 600 !important; letter-spacing: -0.01em; color: var(--ds-ink); }
    h1 + div p { color: var(--ds-muted) !important; font-size: 0.95rem; }
    [data-testid="stSidebar"] {
        border-right: 1px solid var(--ds-line);
    }
    .stButton>button, .stDownloadButton>button {
        border-radius: 4px;
        font-weight: 500;
    }
    [data-testid="stChatMessage"] { border: 1px solid var(--ds-line); border-radius: 8px; }
    .stTabs [data-baseweb="tab"] { font-weight: 500; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("DataScraper")
st.caption("AI-assisted scrape planning, executed by Python, exported to Excel.")


def _model_for(provider: str) -> str:
    if provider == "ollama":
        return settings.ollama_model
    if provider == "claude":
        return settings.anthropic_model
    if provider == "groq":
        return settings.groq_model
    return settings.gemini_model


def _has_key_for(provider: str) -> bool:
    if provider == "ollama":
        return True  # no key needed; reachability is checked at chat time instead
    if provider == "claude":
        return bool(settings.anthropic_api_key)
    if provider == "groq":
        return bool(settings.groq_api_key)
    return bool(settings.gemini_api_key)


_PROVIDER = settings.ai_provider
_MODEL = _model_for(_PROVIDER)

if _PROVIDER == "ollama" and _running_on_streamlit_cloud():
    st.error("This deployment is configured for Ollama, which only runs locally. Set AI_PROVIDER in Secrets.")
elif not _has_key_for(_PROVIDER):
    st.error(
        f"No API key configured for the '{_PROVIDER}' provider. "
        "Add the key under Secrets (Cloud) or .env (local) — do not set GEMINI_MODEL in Secrets."
    )

def _init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "planner" not in st.session_state:
        st.session_state.planner = None
    if "plan" not in st.session_state:
        st.session_state.plan = None
    if "last_excel" not in st.session_state:
        st.session_state.last_excel = None
    if "last_rows" not in st.session_state:
        st.session_state.last_rows = 0


def _preview_sites(plan: ScrapePlan):
    results = []
    for site in plan.sites:
        fields = plan.resolved_fields_for(site)
        results.append(preview_url(site.start_url, site.list_selector, fields))
    return results


def _available_plans() -> list[tuple[str, Path]]:
    """Bundled examples (shipped in repo) + plans saved this session/machine."""
    items: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for folder, suffix in (
        (settings.plans_dir, ""),
        (ROOT / "examples" / "plans", " · bundled"),
    ):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            key = path.name
            if key in seen:
                continue
            seen.add(key)
            items.append((f"{path.name}{suffix}", path))
    return items


def _load_plan_bytes(raw: bytes) -> ScrapePlan:
    import json

    return ScrapePlan.model_validate(json.loads(raw.decode("utf-8")))


_init_state()

with st.sidebar:
    st.caption(f"Provider: {_PROVIDER} ({_MODEL})")
    if st.button("Reset chat"):
        st.session_state.messages = []
        st.session_state.planner = None
        st.session_state.plan = None
        st.rerun()

    st.divider()
    st.subheader("Load a plan")
    plan_options = _available_plans()
    if plan_options:
        labels = [label for label, _ in plan_options]
        chosen_label = st.selectbox("Saved / bundled plans", labels, key="plan_select")
        chosen_path = dict(plan_options)[chosen_label]
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Load", use_container_width=True):
                plan = load_plan(chosen_path)
                st.session_state.plan = plan
                st.success(f"Loaded “{plan.title}” — open the Plan tab.")
        with c2:
            if st.button("Load & run", type="primary", use_container_width=True):
                plan = load_plan(chosen_path)
                st.session_state.plan = plan
                with st.spinner("Scraping… this can take a while for large catalogs"):
                    engine = ScrapeEngine()
                    rows = engine.run(plan)
                    path = rows_to_excel(rows, filename=(plan.title[:40] or "scrape"))
                    st.session_state.last_excel = path
                    st.session_state.last_rows = len(rows)
                    for w in engine.coverage_warnings:
                        st.warning(w)
                st.success(f"Got {len(rows)} rows → {path.name}")
    else:
        st.caption("No plans on disk yet.")

    uploaded = st.file_uploader("Upload plan JSON", type=["json"], key="plan_upload")
    if uploaded is not None:
        if st.button("Load uploaded plan", use_container_width=True):
            try:
                plan = _load_plan_bytes(uploaded.getvalue())
                # Persist into the writable plans dir so it shows in the list next time
                saved = save_plan(plan)
                st.session_state.plan = plan
                st.success(f"Loaded “{plan.title}” (saved as {saved.name}). Open the Plan tab.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not load plan: {exc}")


tab_chat, tab_plan, tab_results = st.tabs(["Chat", "Plan", "Results"])

with tab_chat:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.chat_input("Describe what to scrape (paste listing URLs when you have them)")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        try:
            # Rebuild the client from current settings on every turn — cheap for
            # gemini/groq/claude (no network call in ping()), and means a changed
            # .env/Secrets model takes effect immediately instead of staying pinned
            # to whatever was configured when this browser session first opened.
            client = AIClient()
            client.ping()
            if st.session_state.planner is None:
                st.session_state.planner = PlannerSession(client)
            else:
                st.session_state.planner.client = client
            planner: PlannerSession = st.session_state.planner
            with st.chat_message("assistant"):
                with st.spinner("Thinking…"):
                    reply, maybe_plan = planner.ask(prompt)
                st.markdown(reply)
                st.session_state.messages.append({"role": "assistant", "content": reply})
                if maybe_plan:
                    with st.spinner("Live preview…"):
                        plan, previews, transcript = planner.refine_with_preview(maybe_plan)
                    for extra in transcript:
                        st.markdown(extra)
                        st.session_state.messages.append({"role": "assistant", "content": extra})
                    st.session_state.plan = plan
                    st.info("Plan captured — open the Plan tab to review and run.")
        except AIError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Unexpected error: {exc}")

with tab_plan:
    plan: Optional[ScrapePlan] = st.session_state.plan
    if not plan:
        st.write("No plan yet — chat first, or load / upload one from the sidebar.")
    else:
        st.subheader(plan.title)
        st.write(plan.goal)
        st.json(plan.model_dump())
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Save plan"):
                path = save_plan(plan)
                st.success(f"Saved {path}")
        with col2:
            if st.button("Run scrape", type="primary"):
                with st.spinner("Scraping… large catalogs take time"):
                    try:
                        previews = _preview_sites(plan)
                        weak = any((p.get("count") or 0) <= 1 for p in previews if p.get("ok"))
                        if weak:
                            st.warning("Live preview looks thin (≤1 rows on a site). Continuing anyway.")
                        engine = ScrapeEngine()
                        rows = engine.run(plan)
                        path = rows_to_excel(rows, filename=(plan.title[:40] or "scrape"))
                        st.session_state.last_excel = path
                        st.session_state.last_rows = len(rows)
                        for w in engine.coverage_warnings:
                            st.warning(w)
                        st.success(f"Collected {len(rows)} rows")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Scrape failed: {exc}")

with tab_results:
    path = st.session_state.last_excel
    if not path:
        st.write("No results yet.")
    else:
        path = Path(path)
        st.write(f"**{st.session_state.last_rows}** rows → `{path}`")
        if path.exists():
            data = path.read_bytes()
            st.download_button(
                "Download Excel",
                data=data,
                file_name=path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            try:
                import pandas as pd

                st.dataframe(pd.read_excel(path).head(100))
            except Exception:  # noqa: BLE001
                pass
