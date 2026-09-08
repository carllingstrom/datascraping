from __future__ import annotations

from typing import Dict, List, Optional

import httpx

from app.config import settings


class AIError(RuntimeError):
    pass


class AIClient:
    """Thin unified chat client for Ollama (local) and Claude (Anthropic)."""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.provider = (provider or settings.ai_provider).strip().lower()
        if self.provider not in {"ollama", "claude"}:
            raise AIError(f"Unknown AI_PROVIDER '{self.provider}'. Use ollama or claude.")
        if self.provider == "ollama":
            self.model = model or settings.ollama_model
        else:
            self.model = model or settings.anthropic_model
            if not settings.anthropic_api_key:
                raise AIError(
                    "ANTHROPIC_API_KEY is missing. Set it in .env or switch AI_PROVIDER=ollama."
                )

    def chat(self, messages: List[Dict[str, str]], system: Optional[str] = None) -> str:
        if self.provider == "ollama":
            return self._ollama(messages, system)
        return self._claude(messages, system)

    def ping(self) -> str:
        """Verify the provider is reachable. Returns a short status string or raises AIError."""
        if self.provider == "ollama":
            url = f"{settings.ollama_base_url}/api/tags"
            try:
                with httpx.Client(timeout=5.0) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
            except httpx.ConnectError as exc:
                raise AIError(
                    "Ollama is not running (nothing at "
                    f"{settings.ollama_base_url}).\n"
                    "Fix:\n"
                    "  1) Install: https://ollama.com/download\n"
                    "  2) Open the Ollama app (or run: ollama serve)\n"
                    f"  3) Pull a model: ollama pull {self.model}\n"
                    "  4) Re-run: python main.py chat\n"
                    "Or use Claude: python main.py chat --provider claude"
                ) from exc
            except httpx.HTTPError as exc:
                raise AIError(f"Ollama responded with an error: {exc}") from exc

            names = [m.get("name", "") for m in data.get("models") or []]
            if not any(self.model in n or n.startswith(self.model) for n in names):
                raise AIError(
                    f"Ollama is up, but model '{self.model}' is not installed.\n"
                    f"Available: {', '.join(names) or '(none)'}\n"
                    f"Run: ollama pull {self.model}"
                )
            return f"ollama ok ({self.model})"

        # Claude: light check that the SDK + key look usable
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise AIError("anthropic package missing. Run: pip install anthropic") from exc
        if not settings.anthropic_api_key:
            raise AIError("ANTHROPIC_API_KEY missing in .env")
        return f"claude ready ({self.model})"

    def _ollama(self, messages: List[Dict[str, str]], system: Optional[str]) -> str:
        payload_messages: List[Dict[str, str]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)
        url = f"{settings.ollama_base_url}/api/chat"
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    url,
                    json={
                        "model": self.model,
                        "messages": payload_messages,
                        "stream": False,
                    },
                )
                if resp.status_code >= 400:
                    detail = ""
                    try:
                        detail = (resp.json() or {}).get("error") or resp.text
                    except Exception:  # noqa: BLE001
                        detail = resp.text
                    if resp.status_code == 404 and "not found" in str(detail).lower():
                        raise AIError(
                            f"Ollama model '{self.model}' not found.\n"
                            f"Run: ollama pull {self.model}\n"
                            f"Details: {detail}"
                        )
                    raise AIError(f"Ollama error ({resp.status_code}): {detail}")
                data = resp.json()
        except AIError:
            raise
        except httpx.ConnectError as exc:
            raise AIError(
                f"Cannot reach Ollama at {settings.ollama_base_url}. Is `ollama serve` running?"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIError(f"Ollama request failed: {exc}") from exc

        message = data.get("message") or {}
        content = message.get("content")
        if not content:
            raise AIError(f"Empty Ollama response: {data}")
        return content.strip()

    def _claude(self, messages: List[Dict[str, str]], system: Optional[str]) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise AIError("anthropic package not installed. Run: pip install anthropic") from exc

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        try:
            resp = client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise AIError(f"Claude request failed: {exc}") from exc

        parts = []
        for block in resp.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        content = "".join(parts).strip()
        if not content:
            raise AIError("Empty Claude response")
        return content
