"""Unit tests for the TTS provider abstraction and NullTtsProvider."""

import json
from typing import Any

import httpx
import pytest

from app.integrations.tts.exceptions import (
    TtsProviderError,
    TtsTransportError,
)
from app.integrations.tts.openai_compatible import OpenAiTtsProvider
from app.integrations.tts.provider import NullTtsProvider


def make_provider(handler: Any, retry_attempts: int = 2) -> OpenAiTtsProvider:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAiTtsProvider(
        http_client=http,
        base_url="https://tts.example/v1",
        api_key="sk-tts",
        model="tts-1",
        default_voice="alloy",
        timeout_seconds=5.0,
        retry_attempts=retry_attempts,
    )


async def test_synthesize_returns_audio_bytes() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fake-mp3-bytes")

    provider = make_provider(handler)
    audio = await provider.synthesize("hello", "alloy", "us")
    assert audio == b"fake-mp3-bytes"


async def test_synthesize_uses_model_and_voice() -> None:
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, content=b"mp3")

    provider = make_provider(handler)
    await provider.synthesize("hello", "onyx", "uk")

    assert payloads[0]["model"] == "tts-1"
    assert payloads[0]["voice"] == "onyx"
    assert payloads[0]["input"] == "hello"


async def test_rate_limit_is_retried() -> None:
    calls: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, content=b"mp3")

    provider = make_provider(handler)
    audio = await provider.synthesize("hello", "alloy", "us")

    assert audio == b"mp3"
    assert len(calls) == 2


async def test_server_error_exhausts_retries() -> None:
    calls: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="boom")

    provider = make_provider(handler)
    with pytest.raises(TtsProviderError) as exc_info:
        await provider.synthesize("hello", "alloy", "us")

    assert exc_info.value.status_code == 500
    assert len(calls) == 3  # 1 attempt + 2 retries


async def test_bad_request_is_not_retried() -> None:
    calls: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="bad input")

    provider = make_provider(handler)
    with pytest.raises(TtsProviderError) as exc_info:
        await provider.synthesize("hello", "alloy", "us")

    assert exc_info.value.status_code == 400
    assert exc_info.value.retryable is False
    assert len(calls) == 1


async def test_timeout_is_transport_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    provider = make_provider(handler)
    with pytest.raises(TtsTransportError):
        await provider.synthesize("hello", "alloy", "us")


async def test_empty_audio_is_provider_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    provider = make_provider(handler)
    with pytest.raises(TtsProviderError):
        await provider.synthesize("hello", "alloy", "us")


async def test_null_provider_returns_empty_bytes() -> None:
    provider = NullTtsProvider()
    audio = await provider.synthesize("hello", "alloy", "us")
    assert audio == b""
    assert provider.name == "null"
