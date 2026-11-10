"""TTS service: audio synthesis with a disk + database cache.

Cache key is sha256(text + voice + accent + provider_version) — the
provider version (model) is mandatory in the key, so a voice or model
change invalidates cached audio. Audio files live on disk
(``tts_cache_dir``); the database stores only cache metadata.

Transaction boundaries are short: ownership check, cache lookup and the
cache write each use their own session; the LLM/TTS provider call runs
outside any transaction. Telemetry (``tts_calls``) is written
best-effort in its own session and never changes the operation result.
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.db.session import SessionFactory, session_scope
from app.domain.srs import utc_now
from app.integrations.tts.exceptions import TtsError
from app.integrations.tts.provider import TtsProvider
from app.repositories import (
    ExpressionProgressRepository,
    ExpressionRepository,
    TtsAudioRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TtsAudioResult:
    """The cached/synthesized audio for one phrase."""

    cache_key: str
    file_path: str
    cache_hit: bool


@dataclass(frozen=True)
class TtsUnavailable:
    """Why audio could not be produced (typed, no internals)."""

    reason: Literal["tts_disabled", "tts_error", "expression_not_found"]


def cache_audio_key(
    *, text: str, voice: str, accent: str, provider_version: str
) -> str:
    """sha256 of text + voice + accent + provider_version.

    The provider version (model) is mandatory in the key: a voice or
    model change must invalidate cached audio.
    """
    raw = f"{text}|{voice}|{accent}|{provider_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TtsService:
    """Synthesizes audio with caching, telemetry and ownership checks."""

    def __init__(
        self,
        session_factory: SessionFactory,
        provider: TtsProvider | None,
        cache_dir: str,
        default_voice: str = "alloy",
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._cache_dir = Path(cache_dir)
        self.default_voice = default_voice

    async def get_or_synthesize(
        self,
        *,
        user_id: int,
        expression_id: int,
        voice: str,
        accent: str,
    ) -> TtsAudioResult | TtsUnavailable:
        """Return cached/synthesized audio for one expression.

        Ownership: the expression must have been served to this user (a
        progress row exists); a foreign expression is reported as not
        found without any TTS work.
        """
        if self._provider is None:
            return TtsUnavailable(reason="tts_disabled")

        async with session_scope(self._session_factory) as session:
            progress = await ExpressionProgressRepository(session).get_by_pair(
                user_id=user_id, expression_id=expression_id
            )
            if progress is None:
                return TtsUnavailable(reason="expression_not_found")
            expression = await ExpressionRepository(session).get_by_id(expression_id)
            if expression is None:
                return TtsUnavailable(reason="expression_not_found")
            text = expression.text

        key = cache_audio_key(
            text=text,
            voice=voice,
            accent=accent,
            provider_version=self._provider.version,
        )

        async with session_scope(self._session_factory) as session:
            cached = await TtsAudioRepository(session).get_by_key(key)
            if cached is not None and Path(cached.file_path).is_file():
                await self._record(
                    provider=self._provider.name,
                    voice=voice,
                    accent=accent,
                    cache_hit=True,
                    latency_ms=0,
                )
                return TtsAudioResult(
                    cache_key=key,
                    file_path=cached.file_path,
                    cache_hit=True,
                )

        latency_started = utc_now()
        try:
            audio = await self._provider.synthesize(
                text=text, voice=voice, accent=accent
            )
        except TtsError as error:
            logger.warning(
                "tts synthesis failed voice=%s accent=%s error_type=%s",
                voice,
                accent,
                type(error).__name__,
            )
            await self._record(
                provider=self._provider.name,
                voice=voice,
                accent=accent,
                cache_hit=False,
                latency_ms=0,
                error_kind=type(error).__name__,
            )
            return TtsUnavailable(reason="tts_error")

        file_path = self._cache_dir / f"{key}.mp3"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = file_path.with_suffix(".tmp")
        tmp_path.write_bytes(audio)
        tmp_path.replace(file_path)

        async with session_scope(self._session_factory) as session:
            row = await TtsAudioRepository(session).create(
                cache_key=key,
                text=text,
                provider=self._provider.name,
                provider_version=self._provider.version,
                voice=voice,
                accent=accent,
                file_path=str(file_path),
            )
        await self._record(
            provider=self._provider.name,
            voice=voice,
            accent=accent,
            cache_hit=False,
            latency_ms=int((utc_now() - latency_started).total_seconds() * 1000),
        )
        logger.info(
            "tts synthesized voice=%s accent=%s key=%s",
            voice,
            accent,
            key[:12],
        )
        return TtsAudioResult(
            cache_key=row.cache_key,
            file_path=row.file_path,
            cache_hit=False,
        )

    async def _record(
        self,
        *,
        provider: str,
        voice: str,
        accent: str,
        cache_hit: bool,
        latency_ms: int,
        error_kind: str | None = None,
    ) -> None:
        """Best-effort tts_calls telemetry in its own short session.

        A database failure here is logged and never changes the
        operation result shown to the user.
        """
        from app.integrations.tts.recorder import TtsCallRecorder

        recorder = TtsCallRecorder(self._session_factory)
        try:
            await recorder.record(
                provider=provider,
                voice=voice,
                accent=accent,
                cache_hit=cache_hit,
                latency_ms=latency_ms,
                error_kind=error_kind,
            )
        except Exception:
            logger.warning("tts telemetry write failed (ignored)")
