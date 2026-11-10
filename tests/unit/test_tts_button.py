"""Unit tests for the TTS listen button handler (Stage 6.4.5).

Handlers are called with a mocked TtsService — no real provider, no
network. Ownership is enforced by the service layer (progress row).
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import tts as tts_handlers
from app.bot.handlers.tts import TtsCB
from app.core.config import Settings
from app.services import TtsService
from app.services.tts import TtsUnavailable

SETTINGS = Settings(_env_file=None, database_url="postgresql://u:p@h/db")

TEST_TELEGRAM_ID = 12345


def make_callback() -> Any:
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    callback.message = MagicMock(spec=Message)
    callback.message.answer_voice = AsyncMock()
    return callback


def make_profile_service() -> Any:
    """No profile row → the handler falls back to the US accent."""
    profile_service = AsyncMock()
    profile_service.get_by_user_id = AsyncMock(return_value=None)
    return profile_service


def make_service(result: Any = None) -> Any:
    service = AsyncMock(spec=TtsService)
    service.default_voice = SETTINGS.tts_voice
    service.get_or_synthesize.return_value = result
    return service


async def test_listen_sends_voice_from_cache() -> None:
    """Second press replays from cache: provider not hit (service-level)."""
    audio = SimpleNamespace(cache_key="abc", file_path="/tmp/x.mp3", cache_hit=True)
    service = make_service(audio)
    callback = make_callback()

    await tts_handlers.on_listen(
        callback, TtsCB(expression_id=42), service, make_profile_service()
    )

    service.get_or_synthesize.assert_awaited_once_with(
        user_id=TEST_TELEGRAM_ID,
        expression_id=42,
        voice=SETTINGS.tts_voice,
        accent="us",
    )
    callback.message.answer_voice.assert_awaited_once()
    file_arg = callback.message.answer_voice.await_args.args[0]
    assert file_arg.path.endswith(".mp3")


async def test_listen_disabled_shows_friendly_text() -> None:
    service = make_service(TtsUnavailable(reason="tts_disabled"))
    callback = make_callback()

    await tts_handlers.on_listen(
        callback, TtsCB(expression_id=42), service, make_profile_service()
    )

    callback.message.answer_voice.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.TTS_UNAVAILABLE, show_alert=True)


async def test_listen_tts_error_shows_friendly_text() -> None:
    service = make_service(TtsUnavailable(reason="tts_error"))
    callback = make_callback()

    await tts_handlers.on_listen(
        callback, TtsCB(expression_id=42), service, make_profile_service()
    )

    callback.message.answer_voice.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.TTS_UNAVAILABLE, show_alert=True)


async def test_listen_foreign_expression_is_neutral() -> None:
    """A foreign expression: neutral callback answer, no synthesis."""
    service = make_service(TtsUnavailable(reason="expression_not_found"))
    callback = make_callback()

    await tts_handlers.on_listen(
        callback, TtsCB(expression_id=999), service, make_profile_service()
    )

    callback.answer.assert_awaited_once_with(texts.CHAT_NOT_FOUND, show_alert=False)
    callback.message.answer_voice.assert_not_awaited()


async def test_callback_round_trip() -> None:
    packed = TtsCB(expression_id=42).pack()
    assert TtsCB.unpack(packed).expression_id == 42
