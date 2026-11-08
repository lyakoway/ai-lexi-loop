"""OpenAI-compatible chat-completions provider.

Works with any endpoint speaking the OpenAI chat-completions dialect
(OpenAI, OpenRouter, Ollama, vLLM…) — switching is pure configuration.
Implements the retry policy agreed for Stage 6.1:

- transport errors (connect/timeout) and HTTP 429/5xx are retried up to
  ``retry_attempts`` extra tries, honouring ``Retry-After`` (capped so a
  hostile header cannot stall the request);
- other 4xx (auth, bad request) fail immediately — retrying cannot help;
- the total wall-clock budget is bounded by
  ``timeout_seconds * (retry_attempts + 1)``.
"""

import asyncio
import logging

import httpx

from app.integrations.llm.exceptions import (
    LlmProviderError,
    LlmTransportError,
)
from app.integrations.llm.schemas import (
    LlmRequest,
    LlmResponse,
    LlmUsage,
)

logger = logging.getLogger(__name__)

#: Upper bound for honouring Retry-After; longer server asks are clamped.
_MAX_RETRY_AFTER_SECONDS = 8.0
#: Base of the exponential backoff between retryable attempts (seconds).
_RETRY_BACKOFF_BASE_SECONDS = 0.25


def retry_delay_seconds(
    attempt: int,
    retry_after: float | None,
    remaining_budget: float,
) -> float:
    """Delay before the next retryable attempt.

    Exponential backoff (0.25s, 0.5s, …) or the server-asked
    ``Retry-After`` when it is larger, clamped by both the Retry-After
    cap and the remaining wall-clock budget. No jitter on purpose: with
    at most a couple of retries from a single process there is no herd
    to spread (revisit when multiple app replicas appear).
    """
    backoff: float = _RETRY_BACKOFF_BASE_SECONDS * (2**attempt)
    if retry_after is not None:
        clamped_after = min(float(retry_after), _MAX_RETRY_AFTER_SECONDS)
        backoff = max(backoff, clamped_after)
    return max(0.0, min(backoff, float(remaining_budget)))


class OpenAiCompatibleClient:
    """One completion call per ``complete``; retries live inside it."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout_seconds: float,
        retry_attempts: int,
        max_output_tokens: int,
    ) -> None:
        self._http = http_client
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._default_model = default_model
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = retry_attempts
        self._max_output_tokens = max_output_tokens

    async def complete(self, request: LlmRequest) -> LlmResponse:
        payload = self._build_payload(request)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        deadline = asyncio.get_running_loop().time() + (
            self._timeout_seconds * (self._retry_attempts + 1)
        )
        last_error: Exception | None = None
        # Not every OpenAI-compatible endpoint supports response_format
        # (Ollama builds, older vLLM...). A 400 that rejects the JSON
        # mode drops it once: content correctness is enforced by the
        # structured layer (validation + repair), not by this flag.
        json_mode_dropped = False
        for attempt in range(self._retry_attempts + 1):
            retry_after: float | None = None
            try:
                response = await self._http.post(
                    self._url,
                    json=payload,
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
            except httpx.TimeoutException as error:
                last_error = LlmTransportError(
                    f"provider timed out after {self._timeout_seconds}s"
                )
                _suppress(error)
            except httpx.TransportError as error:
                last_error = LlmTransportError("provider unreachable")
                _suppress(error)
            else:
                if response.status_code == 200:
                    return self._parse_response(response, payload)
                if (
                    response.status_code == 400
                    and payload.get("response_format")
                    and not json_mode_dropped
                ):
                    json_mode_dropped = True
                    payload = {
                        k: v for k, v in payload.items() if k != "response_format"
                    }
                    logger.info(
                        "provider rejected response_format, retrying without it"
                    )
                    retry_after = None
                else:
                    provider_error = LlmProviderError(
                        _provider_error_message(response),
                        status_code=response.status_code,
                    )
                    if not provider_error.retryable:
                        raise provider_error
                    last_error = provider_error
                    retry_after = _parse_retry_after(response)

            if attempt >= self._retry_attempts:
                break
            delay = retry_delay_seconds(
                attempt,
                retry_after,
                deadline - asyncio.get_running_loop().time(),
            )
            if delay <= 0 and deadline - asyncio.get_running_loop().time() <= 0:
                logger.warning(
                    "llm retry budget exhausted before attempt %s", attempt + 2
                )
                break
            await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    def _build_payload(self, request: LlmRequest) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": request.model or self._default_model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
        }
        max_tokens = request.max_output_tokens or self._max_output_tokens
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if request.response_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _parse_response(
        self, response: httpx.Response, payload: dict[str, object]
    ) -> LlmResponse:
        try:
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LlmProviderError(
                "provider returned a malformed success response",
                status_code=200,
            ) from error
        usage_raw = body.get("usage") or {}
        usage = LlmUsage(
            input_tokens=usage_raw.get("prompt_tokens"),
            output_tokens=usage_raw.get("completion_tokens"),
            total_tokens=usage_raw.get("total_tokens"),
        )
        has_usage = any(
            value is not None
            for value in (
                usage.input_tokens,
                usage.output_tokens,
                usage.total_tokens,
            )
        )
        return LlmResponse(
            content=content,
            model=str(body.get("model") or payload["model"]),
            provider="openai_compatible",
            usage=usage if has_usage else None,
            finish_reason=choice.get("finish_reason"),
            request_id=response.headers.get("x-request-id") or body.get("id"),
        )


def _provider_error_message(response: httpx.Response) -> str:
    """A log-safe error description: status and provider text, no secrets."""
    detail = response.text[:200]
    return f"provider responded {response.status_code}" + (
        f": {detail}" if detail else ""
    )


def _parse_retry_after(response: httpx.Response) -> float | None:
    """The server-asked delay in seconds, or ``None`` when absent/invalid."""
    raw: str | None = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _suppress(error: Exception) -> None:
    """Detach the original exception to keep it out of chained messages."""
    logger.debug("llm transport failure: %s", type(error).__name__)
