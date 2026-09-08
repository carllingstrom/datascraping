from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


class Settings:
    ai_provider: str = os.getenv("AI_PROVIDER", "ollama").strip().lower()
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    request_timeout: int = _int("REQUEST_TIMEOUT", 30)
    ollama_timeout: int = _int("OLLAMA_TIMEOUT", 300)
    ollama_num_ctx: int = _int("OLLAMA_NUM_CTX", 8192)
    user_agent: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (compatible; DataScraper/1.0; internal)",
    )
    max_pages_default: int = _int("MAX_PAGES_DEFAULT", 500)
    # 0 = enrich every row that needs a detail fetch (slower, comprehensive)
    enrich_detail_limit: int = _int("ENRICH_DETAIL_LIMIT", 0)
    output_dir: Path = ROOT / os.getenv("OUTPUT_DIR", "output")
    plans_dir: Path = ROOT / os.getenv("PLANS_DIR", "plans")


settings = Settings()
settings.output_dir.mkdir(parents=True, exist_ok=True)
settings.plans_dir.mkdir(parents=True, exist_ok=True)
