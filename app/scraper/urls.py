from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse, urlunparse


_PLACEHOLDER = re.compile(r"(^https?://\.\.?/?$)|(\.\.\.)|(example\.com)|(<url>)|(\byour[-_]?site\b)", re.I)


def normalize_url(url: Optional[str], default_scheme: str = "https") -> Optional[str]:
    if url is None:
        return None
    text = str(url).strip()
    if not text:
        return None
    if text.startswith("//"):
        text = f"{default_scheme}:{text}"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = f"{default_scheme}://{text}"

    parsed = urlparse(text)
    host = (parsed.hostname or "").strip(".")
    if not host or host in {".", ".."} or ".." in host or host.startswith("."):
        return None
    if _PLACEHOLDER.search(text):
        return None
    # reject empty path-only junk like https:///
    if not parsed.netloc:
        return None
    # rebuild without fragments; keep query
    clean = urlunparse(
        (parsed.scheme or default_scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, "")
    )
    return clean


def is_fetchable_url(url: Optional[str]) -> bool:
    return normalize_url(url) is not None
