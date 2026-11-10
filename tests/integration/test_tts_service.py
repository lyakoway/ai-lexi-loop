"""Integration tests for TtsService caching on a real PostgreSQL."""

import os

import pytest
from sqlalchemy import select

from app.db.models import TtsCall
from app.db.session import SessionFactory, session_scope
from app.services.tts import TtsAudioResult, TtsService, TtsUnavailable
from tests.integration.conftest import (
    create_profile_row,
    reset_database,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]


class CountingTtsProvider:
    """Fake provider counting real synthesis calls."""

    def __init__(self, version: str = "tts-1") -> None:
        self.name = "fake"
        self.version = version
        self.calls = 0

    async def synthesize(self, text: str, voice: str, accent: str) -> bytes:
        self.calls += 1
        return f"mp3-{self.calls}".encode()


async def prepare_user(session_factory: SessionFactory, telegram_id: int) -> int:
    async with session_scope(session_factory) as session:
        await reset_database(session)
        profile = await create_profile_row(session, telegram_id=telegram_id)
        return profile.user_id


async def seed_served_expression(
    session_factory: SessionFactory, user_id: int, text: str
) -> int:
    """Insert an expression and serve it to the user (progress row)."""
    async with session_scope(session_factory) as session:
        from app.db.models import Expression, ExpressionProgress

        expression = Expression(
            language="ts",
            text=text,
            translation="перевод",
            ipa="aɪ piː eɪ",
            phonetic_hint_ru="пОдсказка",
            example="example",
            example_translation="пример",
            level="A1",
            topic="everyday",
        )
        session.add(expression)
        await session.flush()
        session.add(ExpressionProgress(user_id=user_id, expression_id=expression.id))
        await session.flush()
        return expression.id


async def test_cache_miss_then_hit(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """First call synthesizes (miss); second call replays from cache."""
    user_id = await prepare_user(session_factory, 9600)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    provider = CountingTtsProvider()
    service = TtsService(session_factory, provider, cache_dir="data/tts_audio_test")

    first = await service.get_or_synthesize(
        user_id=user_id,
        expression_id=expression_id,
        voice="alloy",
        accent="us",
    )
    assert isinstance(first, TtsAudioResult)
    assert first.cache_hit is False

    second = await service.get_or_synthesize(
        user_id=user_id,
        expression_id=expression_id,
        voice="alloy",
        accent="us",
    )
    assert isinstance(second, TtsAudioResult)
    assert second.cache_hit is True
    assert second.file_path == first.file_path
    assert provider.calls == 1  # second call did NOT hit the provider


async def test_provider_version_change_invalidates_cache(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """A provider version (model) bump must produce a new cache entry."""
    user_id = await prepare_user(session_factory, 9601)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Haste makes waste"
    )

    service_v1 = TtsService(
        session_factory,
        CountingTtsProvider(version="tts-1"),
        cache_dir="data/tts_audio_test",
    )
    first = await service_v1.get_or_synthesize(
        user_id=user_id,
        expression_id=expression_id,
        voice="alloy",
        accent="us",
    )
    assert isinstance(first, TtsAudioResult)
    assert first.cache_hit is False

    # Same version: cache hit.
    hit = await service_v1.get_or_synthesize(
        user_id=user_id,
        expression_id=expression_id,
        voice="alloy",
        accent="us",
    )
    assert isinstance(hit, TtsAudioResult)
    assert hit.cache_hit is True
    assert hit.file_path == first.file_path

    # Version bump: new key → synthesis again (counter 2 total).
    service_v2 = TtsService(
        session_factory,
        CountingTtsProvider(version="tts-2"),
        cache_dir="data/tts_audio_test",
    )
    miss = await service_v2.get_or_synthesize(
        user_id=user_id,
        expression_id=expression_id,
        voice="alloy",
        accent="us",
    )
    assert isinstance(miss, TtsAudioResult)
    assert miss.cache_hit is False


async def test_tts_disabled_reports_unavailable(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """TTS disabled (provider None): graceful degradation."""
    user_id = await prepare_user(session_factory, 9602)
    expression_id = await seed_served_expression(
        session_factory, user_id, "No audio phrase"
    )
    service = TtsService(session_factory, None, cache_dir="data/tts_audio_test")

    result = await service.get_or_synthesize(
        user_id=user_id,
        expression_id=expression_id,
        voice="alloy",
        accent="us",
    )
    assert isinstance(result, TtsUnavailable)
    assert result.reason == "tts_disabled"


async def test_tts_calls_telemetry_written(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9603)
    expression_id = await seed_served_expression(
        session_factory, user_id, "telemetry phrase"
    )

    service = TtsService(
        session_factory,
        CountingTtsProvider(version="tts-1"),
        cache_dir="data/tts_audio_test",
    )
    await service.get_or_synthesize(
        user_id=user_id,
        expression_id=expression_id,
        voice="alloy",
        accent="us",
    )

    async with session_scope(session_factory) as session:
        calls = (await session.execute(select(TtsCall))).scalars().all()
    assert len(calls) == 1
    call = calls[0]
    assert call.provider == "fake"
    assert call.voice == "alloy"
    assert call.accent == "us"
    assert call.cache_hit is False
    assert call.latency_ms >= 0
    assert call.error_kind is None
