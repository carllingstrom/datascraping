"""Streamlit UI for DataScraper — chat to plan, run scrape, download Excel.

Works locally (Ollama or Claude) and on Streamlit Community Cloud (Claude via secrets).
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
    """Map st.secrets into env vars before app.config loads settings."""
    try:
        secrets = st.secrets
    except Exception:  # noqa: BLE001
        return
    keys = (
        "AI_PROVIDER",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "MAX_PAGES_DEFAULT",
        "ENRICH_DETAIL_LIMIT",
        "REQUEST_TIMEOUT",
        "USER_AGENT",
    )
    for key in keys:
        try:
            value = secrets.get(key)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            value = secrets[key] if key in secrets else None
        if value is not None and str(value).strip() != "":
            os.environ[key] = str(value).strip()


def _running_on_streamlit_cloud() -> bool:
    return bool(
        os.getenv("STREAMLIT_SHARING_MODE")
        or os.getenv("STREAMLIT_RUNTIME_ENV")
        or os.getenv("HOSTNAME", "").endswith("streamlit.app")
    )


_apply_streamlit_secrets()

# Prefer Claude on Streamlit Cloud unless explicitly overridden
if _running_on_streamlit_cloud() and not os.getenv("AI_PROVIDER"):
    os.environ["AI_PROVIDER"] = "claude"

from app.ai.client import AIClient, AIError  # noqa: E402
from app.ai.planner import PlannerSession  # noqa: E402
from app.cli import load_plan, save_plan  # noqa: E402
from app.config import settings  # noqa: E402
from app.export.excel import rows_to_excel  # noqa: E402
from app.models import ScrapePlan  # noqa: E402
from app.scraper.engine import ScrapeEngine  # noqa: E402
from app.scraper.preview import preview_url  # noqa: E402

st.set_page_config(page_title="DataScraper", page_icon="📦", layout="wide")
st.title("DataScraper")
st.caption("AI plans the scrape · Python walks the sites · Excel download")

if _running_on_streamlit_cloud():
    st.info(
        "Running on Streamlit Cloud — use **Claude** (set `ANTHROPIC_API_KEY` in App secrets). "
        "Local Ollama is not available here. Large scrapes can take several minutes."
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


def _default_provider_index() -> int:
    provider = (settings.ai_provider or "ollama").lower()
    if _running_on_streamlit_cloud():
        provider = "claude"
    return 0 if provider == "ollama" else 1


_init_state()

with st.sidebar:
    st.header("Settings")
    provider = st.selectbox(
        "AI provider",
        ["ollama", "claude"],
        index=_default_provider_index(),
        help="On Streamlit Cloud, choose Claude and set ANTHROPIC_API_KEY in secrets.",
    )
    model = st.text_input(
        "Model",
        value=settings.ollama_model if provider == "ollama" else settings.anthropic_model,
    )
    st.caption(f"Default max pages: {settings.max_pages_default}")
    if not settings.anthropic_api_key and provider == "claude":
        st.warning("No ANTHROPIC_API_KEY found. Add it under Manage app → Secrets.")
    if st.button("Reset chat"):
        st.session_state.messages = []
        st.session_state.planner = None
        st.session_state.plan = None
        st.rerun()

    st.divider()
    st.subheader("Or run a saved plan")
    plan_files = sorted(settings.plans_dir.glob("*.json"))
    labels = [p.name for p in plan_files]
    chosen = st.selectbox("Plan file", labels) if labels else None
    if chosen and st.button("Load & run plan", type="primary"):
        plan = load_plan(settings.plans_dir / chosen)
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
            if st.session_state.planner is None:
                client = AIClient(provider=provider, model=model or None)
                client.ping()
                st.session_state.planner = PlannerSession(client)
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
        st.write("No plan yet — chat first, or load one from the sidebar.")
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
