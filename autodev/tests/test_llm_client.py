import asyncio
from pathlib import Path
import sys
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402

from autodev.llm_client import LLMClient, LLMTokenBudgetExceeded  # noqa: E402
import autodev.llm_client as llm_client_module  # noqa: E402


def _fake_async_client_factory(script: list[Any], call_log: list[dict[str, Any]]):
    class _FakeAsyncClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]):
            call_log.append({"url": url, "headers": headers, "json": json})
            idx = len(call_log) - 1
            current = script[idx]
            if isinstance(current, Exception):
                raise current
            status_code, payload = current
            return httpx.Response(
                status_code=status_code,
                request=httpx.Request("POST", url),
                json=payload,
            )

    return _FakeAsyncClient


def test_chat_retries_on_retryable_http_status_and_tracks_usage(monkeypatch):
    script = [
        (503, {"error": "upstream unavailable"}),
        (200, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}),
    ]
    call_log: list[dict[str, Any]] = []
    sleep_log: list[int] = []

    async def _fake_sleep(seconds: int):
        sleep_log.append(seconds)

    monkeypatch.setattr(
        llm_client_module.httpx,
        "AsyncClient",
        _fake_async_client_factory(script, call_log),
    )
    monkeypatch.setattr(llm_client_module.asyncio, "sleep", _fake_sleep)

    client = LLMClient(
        base_url="http://127.0.0.1:9999/v1",
        api_key="test",
        model="fake-model",
        timeout_sec=30,
        max_total_tokens=1000,
    )

    out = asyncio.run(client.chat([{"role": "user", "content": "hello"}]))
    assert out == "ok"
    assert len(call_log) == 2
    assert call_log[0]["headers"]["Authorization"] == "Bearer test"
    assert sleep_log == [60]

    usage = client.usage_summary()
    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 5
    assert usage["total_tokens"] == 15
    assert usage["transport_retries"] == 1
    assert usage["chat_calls"] == 1
    assert usage["failed_chat_calls"] == 0


def test_chat_uses_oauth_token_when_api_key_is_missing(monkeypatch):
    script = [
        (200, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}),
    ]
    call_log: list[dict[str, Any]] = []
    monkeypatch.setattr(
        llm_client_module.httpx,
        "AsyncClient",
        _fake_async_client_factory(script, call_log),
    )

    client = LLMClient(
        base_url="http://127.0.0.1:9999/v1",
        api_key=None,
        oauth_token="oauth-test-token",
        model="fake-model",
        timeout_sec=30,
    )

    out = asyncio.run(client.chat([{"role": "user", "content": "hello"}]))
    assert out == "ok"
    assert call_log[0]["headers"]["Authorization"] == "Bearer oauth-test-token"


def test_chat_prefers_api_key_when_both_api_key_and_oauth_token_exist(monkeypatch):
    script = [
        (200, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}),
    ]
    call_log: list[dict[str, Any]] = []
    monkeypatch.setattr(
        llm_client_module.httpx,
        "AsyncClient",
        _fake_async_client_factory(script, call_log),
    )

    client = LLMClient(
        base_url="http://127.0.0.1:9999/v1",
        api_key="legacy-api-key",
        oauth_token="oauth-test-token",
        model="fake-model",
        timeout_sec=30,
    )

    out = asyncio.run(client.chat([{"role": "user", "content": "hello"}]))
    assert out == "ok"
    assert call_log[0]["headers"]["Authorization"] == "Bearer legacy-api-key"


def test_chat_stops_when_token_budget_is_exceeded(monkeypatch):
    script = [
        (200, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25}}),
    ]
    call_log: list[dict[str, Any]] = []
    monkeypatch.setattr(
        llm_client_module.httpx,
        "AsyncClient",
        _fake_async_client_factory(script, call_log),
    )

    client = LLMClient(
        base_url="http://127.0.0.1:9999/v1",
        api_key="test",
        model="fake-model",
        timeout_sec=30,
        max_total_tokens=20,
    )

    with pytest.raises(LLMTokenBudgetExceeded):
        asyncio.run(client.chat([{"role": "user", "content": "hello"}]))

    usage = client.usage_summary()
    assert usage["total_tokens"] == 25
    assert usage["failed_chat_calls"] == 1
    assert usage["max_total_tokens"] == 20
    assert usage["remaining_tokens"] == 0
