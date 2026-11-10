"""OpenAI-compatible TTS provider with retries and bounded budget.

Mirrors the LLM provider structure: one synthesize call is bounded by
``timeout_seconds * (retry_attempts + 1)``; transport errors and
429/5xx are retried inside that budget (with Retry-After honored),
other 4xx fail immediately. Accent has no OpenAI parameter — it is
part of the provider identity/cache key and is expressed through the
voice and the natural reading of the input text.
"""

import asyncio
import logging

import httpx

from app.integrations.tts.exceptions import (
    TtsProviderError,
    TtsTransportError,
)

logger = logging.getLogger(__name__)


class OpenAiTtsProvider:
    """OpenAI-compatible /audio/speech provider returning mp3 bytes."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model: str,
        default_voice: str,
        timeout_seconds: float,
        retry_attempts: int,
    ) -> None:
        self._http = http_client
        self._url = base_url.rstrip("/") + "/audio/speech"
        self._api_key = api_key
        self.model = model
        self.default_voice = default_voice
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = retry_attempts

    @property
    def name(self) -> str:
        return "openai_compatible"

    @property
    def version(self) -> str:
        """Provider version for the cache key: the model identifies the
        voice quality; a model change must invalidate cached audio."""
        return self.model

    async def synthesize(self, text: str, voice: str, accent: str) -> bytes:
        del accent  # OpenAI TTS has no accent parameter (see docstring)
        payload = {
            "model": self.model,
            "voice": voice or self.default_voice,
            "input": text,
            "response_format": "mp3",
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        deadline = asyncio.get_running_loop().time() + (
            self._timeout_seconds * (self._retry_attempts + 1)
        )
        last_error: Exception | None = None
        for attempt in range(self._retry_attempts + 1):
            try:
                response = await self._http.post(
                    self._url,
                    json=payload,
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
            except httpx.TimeoutException as error:
                last_error = TtsTransportError(
                    f"provider timed out after {self._timeout_seconds}s"
                )
                _suppress(error)
            except httpx.TransportError as error:
                last_error = TtsTransportError("provider unreachable")
                _suppress(error)
            else:
                if response.status_code == 200:
                    content = response.content
                    if not content:
                        raise TtsProviderError(
                            "provider returned empty audio",
                            status_code=200,
                        )
                    return content
                provider_error = TtsProviderError(
                    f"provider responded {response.status_code}: "
                    f"{response.text[:200]}",
                    status_code=response.status_code,
                )
                if not provider_error.retryable:
                    raise provider_error
                last_error = provider_error
                if attempt < self._retry_attempts:
                    await self._backoff(
                        response.headers.get("Retry-After"),
                        deadline,
                    )

            if attempt >= self._retry_attempts:
                break
            if deadline - asyncio.get_running_loop().time() <= 0:
                logger.warning("tts retry budget exhausted")
                break
        assert last_error is not None
        raise last_error

    async def _backoff(self, retry_after: str | None, deadline: float) -> None:
        try:
            delay = float(retry_after) if retry_after is not None else 0.5 * (2**0)
        except ValueError:
            delay = 0.5
        remaining = deadline - asyncio.get_running_loop().time()
        await asyncio.sleep(max(0.0, min(delay, 8.0, remaining)))


def _suppress(error: Exception) -> None:
    """Keep original exceptions out of chained messages (no secrets)."""
    logger.debug("tts transport failure: %s", type(error).__name__)
