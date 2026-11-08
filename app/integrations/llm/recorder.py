"""Best-effort persistence of LLM call telemetry.

The recorder opens its own short session per row and swallows database
failures by design: telemetry must never turn a successful model answer
into an error for the user (and vice versa — an error answer is still
recorded). No prompts, answers, user messages or other content are ever
passed here; only counters, timings and error type names.
"""

import logging

from app.core.context import current_request_id
from app.db.session import SessionFactory, session_scope

logger = logging.getLogger(__name__)


class LlmCallRecorder:
    """Writes one ``llm_calls`` row per recorded call outcome."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

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
    ) -> None:
        """Persist the row; failures are logged and swallowed.

        ``estimated_cost`` stays NULL by design: without a verified
        price table the cost is unknown, and made-up numbers are worse
        than none.
        """
        from app.db.models import LlmCall

        try:
            async with session_scope(self._session_factory) as session:
                session.add(
                    LlmCall(
                        provider=provider,
                        model=model,
                        prompt_version=prompt_version,
                        status=status,
                        latency_ms=round(latency_ms),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        estimated_cost=None,
                        error_type=error_type,
                        request_id=current_request_id(),
                    )
                )
        except Exception as error:
            logger.warning(
                "failed to persist llm call telemetry: %s",
                type(error).__name__,
            )
