"""The LLM client contract and its composable wrappers.

Three layers, assembled by :func:`build_llm_client`:

1. a provider implementation (:mod:`app.integrations.llm.openai_compatible`)
   — HTTP, retries and usage extraction;
2. :class:`ObservingLlmClient` — metrics, logs and a best-effort
   ``llm_calls`` row around every attempt;
3. :class:`StructuredClient` — Pydantic validation of JSON answers with
   exactly one repair regeneration.

Business code sees only the protocols defined here, so swapping the
provider (or reordering the layers) never touches services.
"""

import json
import logging
import time
from typing import Protocol, TypeVar, runtime_checkable

import httpx
from pydantic import BaseModel, ValidationError

from app.core import metrics
from app.core.config import Settings
from app.core.context import current_request_id
from app.integrations.llm.exceptions import LlmError, LlmOutputFormatError
from app.integrations.llm.openai_compatible import OpenAiCompatibleClient
from app.integrations.llm.recorder import LlmCallRecorder
from app.integrations.llm.schemas import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
)

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

#: One extra regeneration with a repair instruction when the model's
#: JSON does not validate against the requested schema.
_STRUCTURED_REPAIR_INSTRUCTION = (
    "Your previous answer was not valid JSON matching the required "
    "schema. Reply again with a single JSON object only — no prose, no "
    "markdown fences — that strictly conforms to the schema."
)


@runtime_checkable
class SupportsRecording(Protocol):
    """Anything that persists one telemetry row (best-effort)."""

    async def record(
        self,
        *,
        provider: str,
        model: str,
        prompt_version: str | None,
        status: str,
        latency_ms: float,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        error_type: str | None,
    ) -> None: ...  # pragma: no cover - protocol body


@runtime_checkable
class LlmClient(Protocol):
    """Anything that can complete a :class:`LlmRequest`."""

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Perform one completion; raises LlmError subclasses on failure."""
        ...  # pragma: no cover - protocol body


@runtime_checkable
class StructuredLlmClient(LlmClient, Protocol):
    """A client that can also return schema-validated JSON answers."""

    async def complete_structured(
        self, request: LlmRequest, schema: type[ModelT]
    ) -> ModelT:
        """Complete and validate the answer against ``schema``."""
        ...  # pragma: no cover - protocol body


class StructuredClient:
    """Adds schema validation with a single repair attempt.

    Transport and provider errors from the wrapped client are not
    retried here — content repair is a different failure level and
    keeps its own, separate retry budget.
    """

    def __init__(self, inner: LlmClient) -> None:
        self._inner = inner

    async def complete(self, request: LlmRequest) -> LlmResponse:
        return await self._inner.complete(request)

    async def complete_structured(
        self, request: LlmRequest, schema: type[ModelT]
    ) -> ModelT:
        response = await self._inner.complete(request)
        try:
            return parse_structured(response.content, schema)
        except LlmOutputFormatError:
            logger.info("structured output invalid, retrying with repair instruction")
            repair_request = LlmRequest(
                messages=[
                    *request.messages,
                    LlmMessage(role="assistant", content=response.content),
                    LlmMessage(role="user", content=_STRUCTURED_REPAIR_INSTRUCTION),
                ],
                temperature=request.temperature,
                max_output_tokens=request.max_output_tokens,
                response_schema=schema,
                # Marked so telemetry can tell repair regenerations from
                # first attempts (prompt_version "explain_v1+repair").
                prompt_version=(
                    f"{request.prompt_version}+repair"
                    if request.prompt_version
                    else "repair"
                ),
                model=request.model,
            )
            repair_response = await self._inner.complete(repair_request)
            return parse_structured(repair_response.content, schema)


def parse_structured[ModelT: BaseModel](content: str, schema: type[ModelT]) -> ModelT:
    """Parse a model answer into ``schema``; raise LlmOutputFormatError.

    Tolerates the most common wrapper — a markdown code fence — but no
    more: this is deliberately not a general JSON-repair framework.
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text[:4].lower() == "json":
            text = text[4:].strip()
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise LlmOutputFormatError("model answer is not valid JSON") from error
    try:
        return schema.model_validate(payload)
    except ValidationError as error:
        raise LlmOutputFormatError("model answer does not match the schema") from error


class ObservingLlmClient:
    """Metrics, logging and best-effort ``llm_calls`` recording.

    A failure to persist the telemetry row (or to log) must never turn a
    successful model answer into an error for the caller, and an error
    answer must still be counted.
    """

    def __init__(
        self,
        inner: LlmClient,
        provider_name: str,
        recorder: SupportsRecording,
    ) -> None:
        self._inner = inner
        self._provider_name = provider_name
        self._recorder = recorder

    async def complete(self, request: LlmRequest) -> LlmResponse:
        started = time.monotonic()
        try:
            response = await self._inner.complete(request)
        except LlmError as error:
            await self._finish_failure(request, started, error)
            raise
        await self._finish_success(request, started, response)
        return response

    async def _finish_success(
        self, request: LlmRequest, started: float, response: LlmResponse
    ) -> None:
        latency_ms = (time.monotonic() - started) * 1000
        metrics.inc_counter("llm_calls_total")
        metrics.observe_milliseconds("llm_latency_ms", latency_ms)
        usage = response.usage
        if usage is not None:
            if usage.input_tokens is not None:
                metrics.inc_counter("llm_input_tokens_total", usage.input_tokens)
            if usage.output_tokens is not None:
                metrics.inc_counter("llm_output_tokens_total", usage.output_tokens)
        await self._recorder.record(
            provider=self._provider_name,
            model=response.model,
            prompt_version=request.prompt_version or None,
            status="ok",
            latency_ms=latency_ms,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            error_type=None,
        )
        logger.info(
            "llm call ok provider=%s model=%s latency_ms=%.0f request_id=%s",
            self._provider_name,
            response.model,
            latency_ms,
            current_request_id(),
        )

    async def _finish_failure(
        self, request: LlmRequest, started: float, error: LlmError
    ) -> None:
        latency_ms = (time.monotonic() - started) * 1000
        metrics.inc_counter("llm_calls_total")
        metrics.inc_counter("llm_errors_total")
        await self._recorder.record(
            provider=self._provider_name,
            model=request.model or "",
            prompt_version=request.prompt_version or None,
            status="error",
            latency_ms=latency_ms,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            error_type=type(error).__name__,
        )
        logger.warning(
            "llm call failed provider=%s model=%s latency_ms=%.0f "
            "error_type=%s request_id=%s",
            self._provider_name,
            request.model or "default",
            latency_ms,
            type(error).__name__,
            current_request_id(),
        )


def build_llm_client(
    settings: Settings,
    http_client: httpx.AsyncClient,
    recorder: LlmCallRecorder,
) -> StructuredClient:
    """Compose the configured provider with observability and structure.

    Called only when ``settings.llm_enabled`` is true and the settings
    have passed validation; the HTTP client lifecycle is owned by the
    application lifespan, not by this factory.
    """
    provider = OpenAiCompatibleClient(
        http_client=http_client,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        default_model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        retry_attempts=settings.llm_retry_attempts,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    return StructuredClient(
        inner=ObservingLlmClient(
            inner=provider,
            provider_name="openai_compatible",
            recorder=recorder,
        )
    )
