from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _on_streamlit_cloud() -> bool:
    if os.getenv("STREAMLIT_SHARING_MODE") or os.getenv("STREAMLIT_RUNTIME_ENV"):
        return True
    hostname = (os.getenv("HOSTNAME") or "").lower()
    if "streamlit" in hostname:
        return True
    try:
        if "/mount/src/" in Path.cwd().as_posix():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _writable_base() -> Path:
    """Repo root locally; /tmp on Streamlit Cloud (repo mount is read-only)."""
    if _on_streamlit_cloud():
        base = Path(tempfile.gettempdir()) / "datascraping"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return ROOT


def _ensure_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
        # Prove we can write (Cloud often allows mkdir then fails on write)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return path
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "datascraping" / path.name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


@dataclass
class Settings:
    ai_provider: str
    ollama_base_url: str
    ollama_model: str
    anthropic_api_key: str
    anthropic_model: str
    gemini_api_key: str
    gemini_model: str
    groq_api_key: str
    groq_model: str
    request_timeout: int
    ollama_timeout: int
    ollama_num_ctx: int
    user_agent: str
    max_pages_default: int
    enrich_detail_limit: int
    output_dir: Path
    plans_dir: Path


def load_settings() -> Settings:
    """Read settings from the current environment (call again after Streamlit secrets)."""
    base = _writable_base()
    output_name = os.getenv("OUTPUT_DIR", "output")
    plans_name = os.getenv("PLANS_DIR", "plans")
    # Absolute env paths win; otherwise resolve under writable base
    output_raw = Path(output_name)
    plans_raw = Path(plans_name)
    output_dir = output_raw if output_raw.is_absolute() else base / output_raw
    plans_dir = plans_raw if plans_raw.is_absolute() else base / plans_raw

    return Settings(
        ai_provider=os.getenv("AI_PROVIDER", "ollama").strip().lower(),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        request_timeout=_int("REQUEST_TIMEOUT", 30),
        ollama_timeout=_int("OLLAMA_TIMEOUT", 300),
        ollama_num_ctx=_int("OLLAMA_NUM_CTX", 8192),
        user_agent=os.getenv(
            "USER_AGENT",
            "Mozilla/5.0 (compatible; DataScraper/1.0; internal)",
        ),
        max_pages_default=_int("MAX_PAGES_DEFAULT", 500),
        enrich_detail_limit=_int("ENRICH_DETAIL_LIMIT", 0),
        output_dir=_ensure_dir(output_dir),
        plans_dir=_ensure_dir(plans_dir),
    )


settings = load_settings()


def reload_settings() -> Settings:
    """Re-bind the module-level settings after env/secrets change."""
    global settings
    settings = load_settings()
    return settings
