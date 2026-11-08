"""Unit tests for observability: metrics, telemetry rows, log safety."""

import logging
from typing import Any

import httpx
import pytest

from app.core import metrics
from app.integrations.llm.client import ObservingLlmClient
from app.integrations.llm.exceptions import (
    LlmOutputFormatError,
    LlmProviderError,
)
from app.integrations.llm.openai_compatible import OpenAiCompatibleClient
from app.integrations.llm.schemas import LlmRequest

API_KEY = "sk-super-secret-987"


def make_provider(handler: Any) -> OpenAiCompatibleClient:
    return OpenAiCompatibleClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://llm.example/v1",
        api_key=API_KEY,
        default_model="test-model",
        timeout_seconds=5.0,
        retry_attempts=0,
        max_output_tokens=64,
    )


def ok_handler(content: str = "hi") -> Any:
    return lambda request: httpx.Response(
        200,
        json={
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        },
    )


class FakeRecorder:
    """Collects the telemetry kwargs the client passes."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


def make_request(**overrides: Any) -> LlmRequest:
    values: dict[str, Any] = {"messages": [], "prompt_version": "explain_v1"}
    values.update(overrides)
    return LlmRequest(**values)


@pytest.fixture(autouse=True)
def clean_metrics() -> None:
    metrics.reset_counters()


async def test_success_records_metrics_and_row() -> None:
    recorder = FakeRecorder()
    client = ObservingLlmClient(
        make_provider(ok_handler()), "openai_compatible", recorder
    )

    response = await client.complete(make_request())

    assert response.content == "hi"
    counters = metrics.get_counters()
    assert counters["llm_calls_total"] == 1
    assert counters["llm_input_tokens_total"] == 10
    assert counters["llm_output_tokens_total"] == 20
    assert "llm_errors_total" not in counters
    histogram = metrics.get_histograms().get("llm_latency_ms")
    assert histogram is not None and histogram.count == 1
    assert len(recorder.rows) == 1
    row = recorder.rows[0]
    assert row["status"] == "ok"
    assert row["model"] == "test-model"
    assert row["prompt_version"] == "explain_v1"
    assert row["total_tokens"] == 30


async def test_error_records_metrics_and_row() -> None:
    recorder = FakeRecorder()
    provider = make_provider(lambda request: httpx.Response(401, text="bad key"))
    client = ObservingLlmClient(provider, "openai_compatible", recorder)

    with pytest.raises(LlmProviderError):
        await client.complete(make_request())

    counters = metrics.get_counters()
    assert counters["llm_calls_total"] == 1
    assert counters["llm_errors_total"] == 1
    assert recorder.rows[0]["status"] == "error"
    assert recorder.rows[0]["error_type"] == "LlmProviderError"


async def test_provider_error_message_has_no_api_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = FakeRecorder()
    provider = make_provider(lambda request: httpx.Response(500, text="boom"))
    client = ObservingLlmClient(provider, "openai_compatible", recorder)

    with caplog.at_level(logging.WARNING), pytest.raises(LlmProviderError):
        await client.complete(make_request())

    assert API_KEY not in caplog.text
    assert "error_type=LlmProviderError" in caplog.text


async def test_success_log_has_no_content(caplog: pytest.LogCaptureFixture) -> None:
    recorder = FakeRecorder()
    client = ObservingLlmClient(
        make_provider(ok_handler("private answer text")), "openai_compatible", recorder
    )

    with caplog.at_level(logging.INFO):
        await client.complete(make_request())

    assert "private answer text" not in caplog.text
    assert "llm call ok" in caplog.text


async def test_missing_usage_records_nulls() -> None:
    recorder = FakeRecorder()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"role": "assistant", "content": "x"}}],
            },
        )

    client = ObservingLlmClient(make_provider(handler), "openai_compatible", recorder)

    await client.complete(make_request())

    row = recorder.rows[0]
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None
    assert row["total_tokens"] is None


def test_metrics_render_llm_series() -> None:
    metrics.inc_counter("llm_calls_total", 2)
    metrics.observe_milliseconds("llm_latency_ms", 250.0)
    rendered = metrics.render_prometheus()
    assert "# TYPE llm_calls_total counter" in rendered
    assert "llm_latency_ms_bucket" in rendered
    assert "llm_latency_ms_count 1" in rendered


async def test_repair_call_is_distinguishable_in_telemetry() -> None:
    """Repair regenerations are recorded with a +repair prompt version."""
    from pydantic import BaseModel

    from app.integrations.llm.client import StructuredClient

    class Demo(BaseModel):
        answer: str

    recorder = FakeRecorder()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"role": "assistant", "content": "not json"}}],
            },
        )

    client = StructuredClient(
        inner=ObservingLlmClient(make_provider(handler), "openai_compatible", recorder)
    )

    try:
        await client.complete_structured(
            LlmRequest(messages=[], prompt_version="explain_v1"), Demo
        )
        raise AssertionError("expected LlmOutputFormatError")
    except LlmOutputFormatError:
        pass

    versions = [row["prompt_version"] for row in recorder.rows]
    assert versions == ["explain_v1", "explain_v1+repair"]
