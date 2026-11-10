"""Best-effort persistence of TTS call telemetry (mirrors LLM recorder).

Only metrics are stored: provider, voice, accent, cache flag, latency,
error kind. Never the synthesized audio or the source text.
"""

import logging

from app.db.session import SessionFactory, session_scope

logger = logging.getLogger(__name__)


class TtsCallRecorder:
    """Writes one ``tts_calls`` row per recorded call outcome."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def record(
        self,
        *,
        provider: str,
        voice: str,
        accent: str,
        cache_hit: bool,
        latency_ms: int,
        error_kind: str | None = None,
    ) -> None:
        """Persist the row; failures are logged and never propagate."""
        from app.db.models import TtsCall

        try:
            async with session_scope(self._session_factory) as session:
                session.add(
                    TtsCall(
                        provider=provider,
                        voice=voice,
                        accent=accent,
                        cache_hit=cache_hit,
                        latency_ms=latency_ms,
                        error_kind=error_kind,
                    )
                )
        except Exception as error:
            logger.warning(
                "failed to persist tts call telemetry: %s",
                type(error).__name__,
            )
