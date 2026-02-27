from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx


class LLMTokenBudgetExceeded(RuntimeError):
    """Raised when cumulative token usage crosses configured budget."""


@dataclass
class ModelEndpoint:
    """One LLM backend (base_url + model + auth)."""

    base_url: str
    model: str
    api_key: str | None = None
    oauth_token: str | None = None

    def auth_token(self) -> str:
        token = self.api_key or self.oauth_token
        if token is None:
            raise ValueError("Missing authentication token for model endpoint.")
        return token


class ModelRouter:
    """Select a :class:`ModelEndpoint` by *role_hint* with ordered fallback.

    Construction modes (backwards-compatible):
    * Single-model legacy: ``ModelRouter(endpoints=[ep])``
    * Multi-model: ``ModelRouter(endpoints=[ep_strong, ep_fast], role_mapping={"planner": 0, "implementer": 1})``
    """

    def __init__(
        self,
        endpoints: List[ModelEndpoint],
        role_mapping: Dict[str, int] | None = None,
    ):
        if not endpoints:
            raise ValueError("ModelRouter requires at least one endpoint.")
        self.endpoints = endpoints
        self.role_mapping: Dict[str, int] = role_mapping or {}

    def resolve(self, role_hint: str | None = None) -> ModelEndpoint:
        """Return the best endpoint for *role_hint*, falling back to index 0."""
        if role_hint and role_hint in self.role_mapping:
            idx = self.role_mapping[role_hint]
            if 0 <= idx < len(self.endpoints):
                return self.endpoints[idx]
        return self.endpoints[0]

    def fallback_for(self, current: ModelEndpoint) -> ModelEndpoint | None:
        """Return the next endpoint in the list after *current*, or ``None``."""
        try:
            idx = self.endpoints.index(current)
        except ValueError:
            return None
        if idx + 1 < len(self.endpoints):
            return self.endpoints[idx + 1]
        return None


class LLMClient:
    """OpenAI-compatible Chat Completions client with retry, token budgeting, and multi-model routing."""

    RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
    RETRY_BACKOFF_SECONDS = (60, 120, 300)

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_sec: int = 240,
        *,
        oauth_token: str | None = None,
        max_total_tokens: int | None = None,
        router: ModelRouter | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip() or None
        self.oauth_token = (oauth_token or "").strip() or None
        self.model = model
        if not self.api_key and not self.oauth_token:
            raise ValueError("Either api_key or oauth_token must be provided.")
        self.timeout = timeout_sec
        self.max_total_tokens = max_total_tokens
        self.router = router

        self._usage_totals: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._chat_calls = 0
        self._failed_chat_calls = 0
        self._transport_retries = 0

    @staticmethod
    def _coerce_usage_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float) and value.is_integer():
            return max(0, int(value))
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0
            try:
                return max(0, int(stripped))
            except ValueError:
                return 0
        return 0

    @classmethod
    def _is_retryable_status(cls, status_code: int) -> bool:
        return status_code in cls.RETRYABLE_STATUS_CODES

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code if exc.response is not None else 0
            return LLMClient._is_retryable_status(status)
        return False

    def _update_usage(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self._usage_totals[key] += self._coerce_usage_int(usage.get(key))

        if self.max_total_tokens is not None and self._usage_totals["total_tokens"] > self.max_total_tokens:
            raise LLMTokenBudgetExceeded(
                "LLM token budget exceeded: "
                f"used={self._usage_totals['total_tokens']} max={self.max_total_tokens}"
            )

    def usage_summary(self) -> Dict[str, int | None]:
        remaining = None
        if self.max_total_tokens is not None:
            remaining = max(0, self.max_total_tokens - self._usage_totals["total_tokens"])
        return {
            **self._usage_totals,
            "max_total_tokens": self.max_total_tokens,
            "remaining_tokens": remaining,
            "chat_calls": self._chat_calls,
            "failed_chat_calls": self._failed_chat_calls,
            "transport_retries": self._transport_retries,
        }

    def _auth_headers(self) -> Dict[str, str]:
        token = self.api_key or self.oauth_token
        if token is None:
            raise ValueError("Missing authentication token.")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        *,
        role_hint: str | None = None,
    ) -> str:
        self._chat_calls += 1

        # Resolve endpoint via router when available.
        if self.router and role_hint:
            endpoint = self.router.resolve(role_hint)
        else:
            endpoint = None

        base_url = endpoint.base_url.rstrip("/") if endpoint else self.base_url
        model = endpoint.model if endpoint else self.model
        auth_token = endpoint.auth_token() if endpoint else (self.api_key or self.oauth_token)
        if auth_token is None:
            raise ValueError("Missing authentication token.")
        headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
        url = f"{base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        max_retries = len(self.RETRY_BACKOFF_SECONDS)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(max_retries + 1):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    self._update_usage(data.get("usage"))
                    content = data["choices"][0]["message"]["content"]
                    if not isinstance(content, str):
                        raise ValueError("LLM response content must be a string.")
                    return content
                except Exception as exc:
                    # On retryable error, try fallback endpoint if available.
                    if self._is_retryable_error(exc) and endpoint and self.router:
                        fallback = self.router.fallback_for(endpoint)
                        if fallback is not None:
                            endpoint = fallback
                            base_url = endpoint.base_url.rstrip("/")
                            model = endpoint.model
                            auth_token = endpoint.auth_token()
                            headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
                            url = f"{base_url}/chat/completions"
                            payload["model"] = model

                    if not self._is_retryable_error(exc) or attempt >= max_retries:
                        self._failed_chat_calls += 1
                        raise
                    self._transport_retries += 1
                    await asyncio.sleep(self.RETRY_BACKOFF_SECONDS[attempt])

        self._failed_chat_calls += 1
        raise RuntimeError("LLM request failed without a terminal exception.")
