"""Tests for Telegram command handlers.

Handlers are called directly with mock objects, so no Telegram API or
database is ever touched.
"""

from datetime import date, time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import help as help_handlers
from app.bot.handlers import start as start_handlers
from app.bot.keyboards.navigation import (
    main_menu_keyboard,
    start_setup_keyboard,
)
from app.db.models import Profile, User
from app.repositories import StorageError
from app.services import ProfileService, UserService


def make_message(user_id: int | None = 12345) -> Any:
    """A mock incoming message from a Telegram user."""
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.from_user = SimpleNamespace(id=user_id) if user_id else None
    return message


def make_user_service(side_effect: BaseException | None = None) -> Any:
    """A mock UserService returning a registered user by default."""
    service = AsyncMock(spec=UserService)
    if side_effect is not None:
        service.ensure_registered.side_effect = side_effect
    else:
        service.ensure_registered.return_value = (
            User(id=1),
            True,
        )
    return service


def make_profile() -> Profile:
    """A fully populated stored profile."""
    return Profile(
        id=7,
        user_id=1,
        native_language="ru",
        target_language="en",
        level="B1",
        learning_goal="work",
        topics=["work", "technology"],
        preferred_accent="uk",
        daily_study_minutes=15,
        new_items_per_day=5,
        lesson_time=time(8, 0),
        review_time=time(19, 0),
        timezone="Europe/Moscow",
        created_at=date(2026, 11, 3),
        updated_at=date(2026, 11, 3),
    )


def make_profile_service(profile: Profile | None = None) -> Any:
    """A mock ProfileService returning the given stored profile."""
    service = AsyncMock(spec=ProfileService)
    service.get_profile.return_value = profile
    return service


async def test_cmd_start_routes_new_user_to_onboarding() -> None:
    message = make_message()
    user_service = make_user_service()
    profile_service = make_profile_service(profile=None)

    await start_handlers.cmd_start(message, user_service, profile_service)

    message.answer.assert_awaited_once()
    call = message.answer.await_args
    assert call.args[0] == texts.START_GREETING_NEW
    assert call.kwargs["reply_markup"] == start_setup_keyboard()


async def test_cmd_start_routes_existing_user_to_menu() -> None:
    message = make_message()
    user_service = make_user_service()
    profile_service = make_profile_service(profile=make_profile())

    await start_handlers.cmd_start(message, user_service, profile_service)

    message.answer.assert_awaited_once()
    call = message.answer.await_args
    assert call.args[0] == texts.START_GREETING_EXISTING
    assert call.kwargs["reply_markup"] == main_menu_keyboard()


async def test_cmd_start_survives_storage_failure() -> None:
    """A database failure must not leave the user without a reply."""
    message = make_message()
    user_service = make_user_service(side_effect=StorageError("db is down"))
    profile_service = make_profile_service()

    await start_handlers.cmd_start(message, user_service, profile_service)

    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.args[0]
    assert sent_text.startswith(texts.START_GREETING_NEW)
    assert "недоступен" in sent_text


async def test_cmd_start_without_from_user_answers_nothing() -> None:
    message = make_message(user_id=None)
    user_service = make_user_service()

    await start_handlers.cmd_start(message, user_service, make_profile_service())

    message.answer.assert_not_awaited()


async def test_cmd_help_shows_capabilities() -> None:
    message = make_message()

    await help_handlers.cmd_help(message)

    message.answer.assert_awaited_once_with(texts.HELP_TEXT)


def make_callback(message: Any | None, data: str = "") -> Any:
    """A mock callback query from a Telegram user."""
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.data = data
    callback.from_user = SimpleNamespace(id=12345)
    callback.message = message
    return callback
