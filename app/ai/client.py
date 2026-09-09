from __future__ import annotations

from typing import Dict, List, Optional, Set

import httpx

from app.config import load_settings

PROVIDERS: Set[str] = {"ollama", "claude", "gemini", "groq"}


class AIError(RuntimeError):
    pass


class AIClient:
    """Unified chat client: Ollama (local) + Gemini / Groq / Claude (API)."""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        # Always read current env (Streamlit secrets may have just been applied)
        s = load_settings()
        self.provider = (provider or s.ai_provider).strip().lower()
        if self.provider not in PROVIDERS:
            raise AIError(
                f"Unknown AI_PROVIDER '{self.provider}'. "
                f"Use one of: {', '.join(sorted(PROVIDERS))}."
            )

        if self.provider == "ollama":
            self.model = model or s.ollama_model
            self._api_key = ""
        elif self.provider == "claude":
            self.model = model or s.anthropic_model
            self._api_key = s.anthropic_api_key
            if not self._api_key:
                raise AIError(
                    "ANTHROPIC_API_KEY is missing. Set it in .env / Streamlit secrets, "
                    "or switch to AI_PROVIDER=gemini (free) or ollama (local)."
                )
        elif self.provider == "gemini":
            self.model = model or s.gemini_model
            self._api_key = s.gemini_api_key
            if not self._api_key:
                raise AIError(
                    "GEMINI_API_KEY is missing.\n"
                    "Get a free key: https://aistudio.google.com/apikey\n"
                    "Then set GEMINI_API_KEY in .env or Streamlit secrets."
                )
        else:  # groq
            self.model = model or s.groq_model
            self._api_key = s.groq_api_key
            if not self._api_key:
                raise AIError(
                    "GROQ_API_KEY is missing.\n"
                    "Get a free key: https://console.groq.com/keys\n"
                    "Then set GROQ_API_KEY in .env or Streamlit secrets."
                )

        self._ollama_base_url = s.ollama_base_url
        self._ollama_timeout = s.ollama_timeout
        self._ollama_num_ctx = s.ollama_num_ctx

    def chat(self, messages: List[Dict[str, str]], system: Optional[str] = None) -> str:
        if self.provider == "ollama":
            return self._ollama(messages, system)
        if self.provider == "claude":
            return self._claude(messages, system)
        if self.provider == "gemini":
            return self._openai_compatible(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key=self._api_key,
                messages=messages,
                system=system,
                label="Gemini",
            )
        return self._openai_compatible(
            base_url="https://api.groq.com/openai/v1",
            api_key=self._api_key,
            messages=messages,
            system=system,
            label="Groq",
        )

    def ping(self) -> str:
        """Verify the provider is reachable. Returns a short status string or raises AIError."""
        if self.provider == "ollama":
            return self._ping_ollama()
        if self.provider == "claude":
            try:
                import anthropic  # noqa: F401
            except ImportError as exc:
                raise AIError("anthropic package missing. Run: pip install anthropic") from exc
            if not self._api_key:
                raise AIError("ANTHROPIC_API_KEY missing in .env")
            return f"claude ready ({self.model})"
        if self.provider == "gemini":
            return f"gemini ready ({self.model}) — free-tier API"
        return f"groq ready ({self.model}) — free-tier API"

    def _ping_ollama(self) -> str:
        url = f"{self._ollama_base_url}/api/tags"
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise AIError(
                "Ollama is not running (nothing at "
                f"{self._ollama_base_url}).\n"
                "Fix:\n"
                "  1) Install: https://ollama.com/download\n"
                "  2) Open the Ollama app (or run: ollama serve)\n"
                f"  3) Pull a model: ollama pull {self.model}\n"
                "  4) Re-run: python main.py chat\n"
                "Or use a free cloud API: AI_PROVIDER=gemini / groq"
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

    def _ollama(self, messages: List[Dict[str, str]], system: Optional[str]) -> str:
        payload_messages: List[Dict[str, str]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)
        url = f"{self._ollama_base_url}/api/chat"
        try:
            with httpx.Client(timeout=self._ollama_timeout) as client:
                resp = client.post(
                    url,
                    json={
                        "model": self.model,
                        "messages": payload_messages,
                        "stream": False,
                        "options": {"num_ctx": self._ollama_num_ctx},
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
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise AIError(
                        f"Ollama returned a non-JSON response. Try a shorter message: {exc}"
                    ) from exc
        except AIError:
            raise
        except httpx.ConnectError as exc:
            raise AIError(
                f"Cannot reach Ollama at {self._ollama_base_url}. Is `ollama serve` running?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise AIError(
                f"Ollama request timed out after {self._ollama_timeout}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise AIError(f"Ollama request failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise AIError(f"Ollama request failed unexpectedly: {exc}") from exc

        if not isinstance(data, dict):
            raise AIError(f"Unexpected Ollama response shape: {data!r}")
        message = data.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if not content:
            raise AIError(f"Empty Ollama response: {data}")
        return content.strip()

    def _claude(self, messages: List[Dict[str, str]], system: Optional[str]) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise AIError("anthropic package not installed. Run: pip install anthropic") from exc

        client = anthropic.Anthropic(api_key=self._api_key)
        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        try:
            resp = client.messages.create(**kwargs)
            parts = []
            for block in resp.content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            content = "".join(parts).strip()
        except Exception as exc:  # noqa: BLE001
            raise AIError(f"Claude request failed: {exc}") from exc
        if not content:
            raise AIError("Empty Claude response")
        return content

    def _openai_compatible(
        self,
        *,
        base_url: str,
        api_key: str,
        messages: List[Dict[str, str]],
        system: Optional[str],
        label: str,
    ) -> str:
        """Gemini + Groq both speak the OpenAI chat-completions shape."""
        payload_messages: List[Dict[str, str]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    url,
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": payload_messages,
                        "temperature": 0.2,
                    },
                )
                if resp.status_code >= 400:
                    detail = resp.text
                    try:
                        err = resp.json()
                        detail = str(
                            (err.get("error") or {}).get("message")
                            or err.get("error")
                            or err
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    raise AIError(f"{label} error ({resp.status_code}): {detail}")
                data = resp.json()
        except AIError:
            raise
        except httpx.HTTPError as exc:
            raise AIError(f"{label} request failed: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError(f"Unexpected {label} response: {data!r}") from exc
        if not content or not str(content).strip():
            raise AIError(f"Empty {label} response")
        return str(content).strip()
