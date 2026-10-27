"""Local (Ollama) and optional OpenAI-compatible hosted inference clients."""
from __future__ import annotations

import httpx


class AIError(Exception):
    """Base class for AI failures; message is safe to show to the user."""


class AIUnavailable(AIError):
    pass


class AITimeout(AIError):
    pass


class AIQuotaExceeded(AIError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: int = 120, max_tokens: int = 1024):
        self.base_url, self.model, self.timeout, self.max_tokens = base_url.rstrip("/"), model, timeout, max_tokens

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self.base_url}/api/tags")
                if r.status_code != 200:
                    return False
                names = {m.get("name", "") for m in r.json().get("models", [])}
                return any(n == self.model or n.split(":")[0] == self.model for n in names)
        except (httpx.HTTPError, ValueError):
            return False

    async def chat(self, messages: list[dict]) -> str:
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"num_predict": self.max_tokens}}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise AITimeout("The local model took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise AIUnavailable("Cannot reach Ollama. Is it running (`ollama serve`)?") from exc
        if r.status_code == 404:
            raise AIUnavailable(f"Model '{self.model}' is not installed. Run `ollama pull {self.model}`.")
        if r.status_code != 200:
            raise AIUnavailable(f"Ollama returned HTTP {r.status_code}.")
        try:
            return (r.json()["message"]["content"] or "").strip()
        except (KeyError, ValueError, TypeError) as exc:
            raise AIUnavailable("Ollama returned an unexpected response.") from exc


class OpenAICompatClient:
    """Optional Mode B. Only used if YOU configured it. Quota/billing errors stop requests; no fallback to paid."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60, max_tokens: int = 1024):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.timeout, self.max_tokens = timeout, max_tokens

    async def is_available(self) -> bool:
        return bool(self.base_url and self.model)

    async def chat(self, messages: list[dict]) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise AITimeout("The hosted model took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise AIUnavailable("Cannot reach the hosted AI provider.") from exc
        if r.status_code in (402, 429):
            raise AIQuotaExceeded("The free hosted AI quota is used up. Stopping until it resets (no paid fallback).")
        if r.status_code in (401, 403):
            raise AIUnavailable("Hosted AI rejected the credentials/permissions.")
        if r.status_code != 200:
            raise AIUnavailable(f"Hosted AI returned HTTP {r.status_code}.")
        try:
            return (r.json()["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise AIUnavailable("Hosted AI returned an unexpected response.") from exc
