"""Unit tests for the OpenAI-compatible provider and the retry policy.

All HTTP interaction goes through ``httpx.MockTransport`` — no real API
calls are ever made, and the API key must never surface in errors or
logs.
"""

import json
from typing import Any

import httpx
import pytest

from app.integrations.llm.exceptions import (
    LlmProviderError,
    LlmTransportError,
)
from app.integrations.llm.openai_compatible import OpenAiCompatibleClient
from app.integrations.llm.schemas import LlmMessage, LlmRequest

API_KEY = "sk-secret-key-123"
BASE_URL = "https://llm.example/v1"


def make_client(
    handler: Any,
    *,
    retry_attempts: int = 2,
    timeout: float = 5.0,
) -> OpenAiCompatibleClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return OpenAiCompatibleClient(
        http_client=http,
        base_url=BASE_URL,
        api_key=API_KEY,
        default_model="test-model",
        timeout_seconds=timeout,
        retry_attempts=retry_attempts,
        max_output_tokens=64,
    )


def ok_response(
    content: str = "hello", *, usage: dict[str, int] | None = None
) -> httpx.Response:
    body: dict[str, Any] = {
        "id": "resp-1",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body, headers={"x-request-id": "req-42"})


def make_request(**overrides: Any) -> LlmRequest:
    values: dict[str, Any] = {
        "messages": [LlmMessage(role="user", content="hi")],
    }
    values.update(overrides)
    return LlmRequest(**values)


async def test_success_extracts_content_and_usage() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return ok_response(
            "hello",
            usage={"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        )

    client = make_client(handler)

    response = await client.complete(make_request())

    assert response.content == "hello"
    assert response.provider == "openai_compatible"
    assert response.model == "test-model"
    assert response.finish_reason == "stop"
    assert response.request_id == "req-42"
    assert response.usage is not None
    assert response.usage.total_tokens == 8
    assert seen["auth"] == f"Bearer {API_KEY}"
    assert seen["url"] == BASE_URL + "/chat/completions"
    assert seen["payload"]["model"] == "test-model"
    assert seen["payload"]["max_tokens"] == 64
    assert "response_format" not in seen["payload"]


async def test_missing_usage_is_none() -> None:
    client = make_client(lambda request: ok_response("hi"))

    response = await client.complete(make_request())

    assert response.usage is None


async def test_json_mode_requested_with_schema() -> None:
    from pydantic import BaseModel

    class Demo(BaseModel):
        answer: str

    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return ok_response('{"answer": "ok"}')

    client = make_client(handler)
    await client.complete(make_request(response_schema=Demo))

    assert payloads[0]["response_format"] == {"type": "json_object"}


async def test_rate_limit_is_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return ok_response("after retry")

    client = make_client(handler)
    response = await client.complete(make_request())

    assert response.content == "after retry"
    assert len(calls) == 2


async def test_server_error_exhausts_retries() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="boom")

    client = make_client(handler, retry_attempts=2)

    with pytest.raises(LlmProviderError) as exc_info:
        await client.complete(make_request())

    assert exc_info.value.status_code == 500
    assert exc_info.value.retryable is True
    assert len(calls) == 3  # 1 attempt + 2 retries


async def test_auth_error_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="bad key")

    client = make_client(handler)

    with pytest.raises(LlmProviderError) as exc_info:
        await client.complete(make_request())

    assert exc_info.value.status_code == 401
    assert exc_info.value.retryable is False
    assert len(calls) == 1
    assert API_KEY not in str(exc_info.value)


async def test_bad_request_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="invalid model")

    client = make_client(handler)

    with pytest.raises(LlmProviderError):
        await client.complete(make_request())

    assert len(calls) == 1


async def test_transport_error_exhausts_retries() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    client = make_client(handler)

    with pytest.raises(LlmTransportError):
        await client.complete(make_request())

    assert len(calls) == 3


async def test_timeout_is_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = make_client(handler)

    with pytest.raises(LlmTransportError) as exc_info:
        await client.complete(make_request())

    assert "timed out after" in str(exc_info.value)


async def test_malformed_success_response_is_provider_error() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"unexpected": True}))

    with pytest.raises(LlmProviderError) as exc_info:
        await client.complete(make_request())

    assert "malformed" in str(exc_info.value)


async def test_retry_budget_bounds_total_time() -> None:
    """A tiny deadline stops retries even when attempts remain."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="boom")

    client = make_client(handler, retry_attempts=5, timeout=0.0001)

    with pytest.raises(LlmProviderError):
        await client.complete(make_request())

    assert len(calls) < 6


async def test_request_model_overrides_default() -> None:
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return ok_response()

    client = make_client(handler)
    await client.complete(make_request(model="other-model"))

    assert payloads[0]["model"] == "other-model"


def test_payload_messages_shape() -> None:
    client = make_client(lambda request: ok_response())
    request = make_request(
        messages=[
            LlmMessage(role="system", content="be brief"),
            LlmMessage(role="user", content="hi"),
        ]
    )

    payload = client._build_payload(request)

    assert payload["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]


# ---------------------------------------------------------------------------
# Backoff policy (pure function, no sleeps)
# ---------------------------------------------------------------------------


def test_backoff_grows_exponentially() -> None:
    from app.integrations.llm.openai_compatible import retry_delay_seconds

    assert retry_delay_seconds(0, None, 999.0) == pytest.approx(0.25)
    assert retry_delay_seconds(1, None, 999.0) == pytest.approx(0.5)
    assert retry_delay_seconds(2, None, 999.0) == pytest.approx(1.0)
    assert retry_delay_seconds(3, None, 999.0) == pytest.approx(2.0)


def test_retry_after_wins_when_larger_than_backoff() -> None:
    from app.integrations.llm.openai_compatible import retry_delay_seconds

    assert retry_delay_seconds(0, 3.0, 999.0) == pytest.approx(3.0)


def test_retry_after_is_capped() -> None:
    from app.integrations.llm.openai_compatible import retry_delay_seconds

    assert retry_delay_seconds(0, 120.0, 999.0) == pytest.approx(8.0)


def test_budget_clamps_the_delay() -> None:
    from app.integrations.llm.openai_compatible import retry_delay_seconds

    assert retry_delay_seconds(2, None, 0.4) == pytest.approx(0.4)
    assert retry_delay_seconds(0, None, 0.0) == 0.0
    assert retry_delay_seconds(0, None, -1.0) == 0.0


async def test_429_without_retry_after_still_backs_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delay between attempts comes from backoff, not only Retry-After."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        "app.integrations.llm.openai_compatible.asyncio.sleep", fake_sleep
    )

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429)  # no Retry-After header
        return ok_response("recovered")

    client = make_client(handler)
    response = await client.complete(make_request())

    assert response.content == "recovered"
    assert sleeps == [0.25]  # exponential backoff base


async def test_retry_delays_stay_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recorded delays never exceed the remaining wall-clock budget."""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(
        "app.integrations.llm.openai_compatible.asyncio.sleep", fake_sleep
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = make_client(handler, retry_attempts=2, timeout=0.02)

    with pytest.raises(LlmProviderError):
        await client.complete(make_request())

    assert recorded
    assert all(delay > 0 for delay in recorded)
    # 0.25 + 0.5 clamped into a 0.06s budget: each delay shrinks.
    assert recorded[-1] <= recorded[0]


# ---------------------------------------------------------------------------
# response_format compatibility
# ---------------------------------------------------------------------------


async def test_json_mode_400_drops_response_format_once() -> None:
    """Endpoints without JSON-mode support still get their answer."""
    from pydantic import BaseModel

    class Demo(BaseModel):
        answer: str

    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if "response_format" in payload:
            return httpx.Response(400, text="response_format unsupported")
        return ok_response('{"answer": "ok"}')

    client = make_client(handler)
    response = await client.complete(make_request(response_schema=Demo))

    assert response.content == '{"answer": "ok"}'
    assert len(payloads) == 2
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]


async def test_other_400_with_json_mode_still_fails_without_retry() -> None:
    """A genuine 400 is not masked by the JSON-mode fallback."""
    from pydantic import BaseModel

    class Demo(BaseModel):
        answer: str

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="model does not exist")

    client = make_client(handler)
    from app.integrations.llm.schemas import LlmRequest as LlmRequestT

    with pytest.raises(LlmProviderError):
        await client.complete(
            LlmRequestT(
                messages=[LlmMessage(role="user", content="hi")],
                response_schema=Demo,
            )
        )
    # One JSON-mode drop attempt plus one final failure — no loops.
    assert len(calls) == 2
