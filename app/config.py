from __future__ import annotations

import os
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
    out = Settings(
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
        output_dir=ROOT / os.getenv("OUTPUT_DIR", "output"),
        plans_dir=ROOT / os.getenv("PLANS_DIR", "plans"),
    )
    out.output_dir.mkdir(parents=True, exist_ok=True)
    out.plans_dir.mkdir(parents=True, exist_ok=True)
    return out


settings = load_settings()


def reload_settings() -> Settings:
    """Re-bind the module-level settings after env/secrets change."""
    global settings
    settings = load_settings()
    return settings
