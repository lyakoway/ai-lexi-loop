"""Tests for Telegram command handlers.

Handlers are called directly with mock objects, so no Telegram API or
database is ever touched.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import help as help_handlers
from app.bot.handlers import start as start_handlers
from app.bot.keyboards.start import setup_profile_keyboard
from app.db.models import User
from app.repositories import StorageError
from app.services import UserService


def make_message(user_id: int | None = 12345) -> Any:
    """A mock incoming message from a Telegram user."""
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
    return message


def make_user_service(
    side_effect: BaseException | None = None,
) -> Any:
    """A mock UserService returning a persisted user by default."""
    service = AsyncMock(spec=UserService)
    if side_effect is not None:
        service.ensure_registered.side_effect = side_effect
    else:
        service.ensure_registered.return_value = (
            User(id=1, telegram_user_id=12345),
            True,
        )
    return service


async def test_cmd_start_greets_and_registers_user() -> None:
    message = make_message(user_id=12345)
    service = make_user_service()

    await start_handlers.cmd_start(message, service)

    service.ensure_registered.assert_awaited_once_with(12345)
    message.answer.assert_awaited_once()
    call = message.answer.await_args
    assert call.args[0] == texts.START_GREETING
    # The greeting must carry the profile-setup button.
    assert call.kwargs["reply_markup"] == setup_profile_keyboard()


async def test_cmd_start_survives_storage_failure() -> None:
    """A database failure must not leave the user without a reply."""
    message = make_message(user_id=12345)
    service = make_user_service(side_effect=StorageError("db is down"))

    await start_handlers.cmd_start(message, service)

    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.args[0]
    assert sent_text.startswith(texts.START_GREETING)
    assert "недоступен" in sent_text


async def test_cmd_start_without_from_user_skips_registration() -> None:
    message = make_message(user_id=None)
    service = make_user_service()

    await start_handlers.cmd_start(message, service)

    service.ensure_registered.assert_not_awaited()
    message.answer.assert_awaited_once()


async def test_cmd_help_shows_capabilities() -> None:
    message = make_message()

    await help_handlers.cmd_help(message)

    message.answer.assert_awaited_once_with(texts.HELP_TEXT)


def make_callback(message: Any | None) -> Any:
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.message = message
    return callback


async def test_setup_button_answers_with_teaser() -> None:
    message = make_message()
    callback = make_callback(message)

    await start_handlers.on_setup_profile(callback)

    callback.answer.assert_awaited_once()
    message.answer.assert_awaited_once_with(texts.SETUP_TEASER)


async def test_setup_button_without_message_does_not_crash() -> None:
    callback = make_callback(None)

    await start_handlers.on_setup_profile(callback)

    callback.answer.assert_awaited_once()


def test_setup_keyboard_carries_callback_data() -> None:
    keyboard = setup_profile_keyboard()
    button = keyboard.inline_keyboard[0][0]
    assert button.callback_data == texts.CALLBACK_SETUP_PROFILE
    assert button.text == texts.SETUP_BUTTON
