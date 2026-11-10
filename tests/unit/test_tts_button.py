"""Unit tests for the TTS listen button handler (Stage 6.4.5/6.4.7).

Handlers are called with a mocked TtsService — no real provider, no
network. Ownership is enforced by the service layer (progress row).
Since Stage 6.4.7 the voice comes from the language profile config
(accenent -> voice), falling back to the profile defaults.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import tts as tts_handlers
from app.bot.handlers.tts import TtsCB
from app.services import TtsService
from app.services.tts import TtsUnavailable

TEST_TELEGRAM_ID = 12345
TEST_USER_ID = 11


def make_callback() -> Any:
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    callback.message = MagicMock(spec=Message)
    callback.message.answer_voice = AsyncMock()
    return callback


def make_user_service() -> Any:
    user_service = AsyncMock()
    user_service.ensure_registered.return_value = (
        SimpleNamespace(id=TEST_USER_ID),
        None,
    )
    return user_service


def make_profile_service(profile: Any = None) -> Any:
    profile_service = AsyncMock()
    profile_service.get_profile.return_value = profile
    return profile_service


def make_service(result: Any = None) -> Any:
    service = AsyncMock(spec=TtsService)
    service.get_or_synthesize.return_value = result
    return service


async def test_listen_sends_voice_from_cache() -> None:
    """No profile row: US accent of the default (English) profile."""
    audio = SimpleNamespace(cache_key="abc", file_path="/tmp/x.mp3", cache_hit=True)
    service = make_service(audio)
    callback = make_callback()

    await tts_handlers.on_listen(
        callback,
        TtsCB(expression_id=42),
        make_user_service(),
        make_profile_service(),
        service,
    )

    service.get_or_synthesize.assert_awaited_once_with(
        user_id=TEST_USER_ID,
        expression_id=42,
        voice="alloy",
        accent="us",
    )
    callback.message.answer_voice.assert_awaited_once()
    file_arg = callback.message.answer_voice.await_args.args[0]
    assert file_arg.path.endswith(".mp3")


async def test_voice_comes_from_language_profile_config() -> None:
    """UK accent of an English profile → the configured UK voice."""
    profile = SimpleNamespace(preferred_accent="uk", target_language="en")
    service = make_service(
        SimpleNamespace(cache_key="k", file_path="/tmp/x.mp3", cache_hit=False)
    )

    await tts_handlers.on_listen(
        make_callback(),
        TtsCB(expression_id=42),
        make_user_service(),
        make_profile_service(profile),
        service,
    )

    assert service.get_or_synthesize.await_args.kwargs["voice"] == "onyx"
    assert service.get_or_synthesize.await_args.kwargs["accent"] == "uk"


async def test_listen_disabled_shows_friendly_text() -> None:
    service = make_service(TtsUnavailable(reason="tts_disabled"))
    callback = make_callback()

    await tts_handlers.on_listen(
        callback,
        TtsCB(expression_id=42),
        make_user_service(),
        make_profile_service(),
        service,
    )

    callback.message.answer_voice.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.TTS_UNAVAILABLE, show_alert=True)


async def test_listen_tts_error_shows_friendly_text() -> None:
    service = make_service(TtsUnavailable(reason="tts_error"))
    callback = make_callback()

    await tts_handlers.on_listen(
        callback,
        TtsCB(expression_id=42),
        make_user_service(),
        make_profile_service(),
        service,
    )

    callback.message.answer_voice.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.TTS_UNAVAILABLE, show_alert=True)


async def test_listen_foreign_expression_is_neutral() -> None:
    """A foreign expression: neutral callback answer, no synthesis."""
    service = make_service(TtsUnavailable(reason="expression_not_found"))
    callback = make_callback()

    await tts_handlers.on_listen(
        callback,
        TtsCB(expression_id=999),
        make_user_service(),
        make_profile_service(),
        service,
    )

    callback.answer.assert_awaited_once_with(texts.CHAT_NOT_FOUND, show_alert=False)
    callback.message.answer_voice.assert_not_awaited()


async def test_callback_round_trip() -> None:
    packed = TtsCB(expression_id=42).pack()
    assert TtsCB.unpack(packed).expression_id == 42
