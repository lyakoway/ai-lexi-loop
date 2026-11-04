"""Tests for the settings editor handlers."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import settings as settings_handlers
from app.bot.keyboards.callbacks import SettingsCB
from app.db.models import User
from app.repositories import StorageError
from app.schemas.profile import ProfileUpdate
from app.services import ProfileNotConfiguredError, ProfileService, UserService

TEST_USER = User(id=11)


def make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=12345)
    return FSMContext(storage=storage, key=key)


def make_callback() -> Any:
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.from_user = type("U", (), {"id": 12345})()
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    return callback


def make_services(profile: Any) -> tuple[Any, Any]:
    user_service = AsyncMock(spec=UserService)
    user_service.ensure_registered.return_value = (TEST_USER, False)
    profile_service = AsyncMock(spec=ProfileService)
    profile_service.get_profile.return_value = profile
    return user_service, profile_service


def make_profile() -> Any:
    from datetime import date, time

    from app.db.models import Profile

    return Profile(
        id=5,
        user_id=TEST_USER.id,
        native_language="ru",
        target_language="en",
        level="B1",
        learning_goal="work",
        topics=["work"],
        preferred_accent="uk",
        daily_study_minutes=15,
        new_items_per_day=5,
        lesson_time=time(8, 0),
        review_time=time(19, 0),
        timezone="Europe/Moscow",
        created_at=date(2026, 11, 3),
        updated_at=date(2026, 11, 3),
    )


def set_cb(action: str, field: str = "", value: str = "") -> SettingsCB:
    return SettingsCB(action=action, field=field, value=value)


async def test_apply_single_field_updates_profile() -> None:
    callback = make_callback()
    user_service, profile_service = make_services(make_profile())

    await settings_handlers.on_apply(
        callback, set_cb("apply", "level", "C1"), user_service, profile_service
    )

    profile_service.update_profile.assert_awaited_once()
    args = profile_service.update_profile.await_args.args
    assert args[0] == TEST_USER.id
    patch: ProfileUpdate = args[1]
    assert patch.level is not None and patch.level.value == "C1"
    assert patch.timezone is None
    callback.answer.assert_awaited_with(texts.SETTINGS_FIELD_SAVED)


async def test_apply_rejects_invalid_value() -> None:
    callback = make_callback()
    user_service, profile_service = make_services(make_profile())

    await settings_handlers.on_apply(
        callback, set_cb("apply", "level", "Z9"), user_service, profile_service
    )

    profile_service.update_profile.assert_not_awaited()
    callback.answer.assert_awaited_with(texts.ONBOARDING_INVALID_ANSWER)


async def test_apply_rejects_unknown_field() -> None:
    callback = make_callback()
    user_service, profile_service = make_services(make_profile())

    await settings_handlers.on_apply(
        callback,
        set_cb("apply", "user_id", "42"),
        user_service,
        profile_service,
    )

    profile_service.update_profile.assert_not_awaited()
    callback.answer.assert_awaited_with("Неизвестная настройка")


async def test_apply_reports_missing_profile() -> None:
    callback = make_callback()
    user_service, profile_service = make_services(None)
    profile_service.update_profile.side_effect = ProfileNotConfiguredError("nope")

    await settings_handlers.on_apply(
        callback, set_cb("apply", "level", "A1"), user_service, profile_service
    )

    callback.answer.assert_awaited_with("Профиль не найден, начните с /start")


async def test_apply_storage_failure_is_reported() -> None:
    callback = make_callback()
    user_service, profile_service = make_services(make_profile())
    profile_service.update_profile.side_effect = StorageError("db down")

    await settings_handlers.on_apply(
        callback, set_cb("apply", "level", "A1"), user_service, profile_service
    )

    callback.answer.assert_awaited_with("Не удалось сохранить, попробуйте позже")


async def test_edit_topics_opens_fsm_editor_with_current_selection() -> None:
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services(make_profile())

    await settings_handlers.on_edit_field(
        callback, set_cb("edit", "topics"), state, user_service, profile_service
    )

    assert await state.get_state() == settings_handlers.SettingsFlow.topics.state
    assert (await state.get_data())["selected_topics"] == ["work"]


async def test_topics_toggle_updates_selection() -> None:
    callback = make_callback()
    state = make_state()
    await state.set_state(settings_handlers.SettingsFlow.topics)
    await state.update_data(selected_topics=["work"])

    await settings_handlers.on_topic_toggle(
        callback, set_cb("topic_toggle", "topics", "music"), state
    )
    assert (await state.get_data())["selected_topics"] == ["work", "music"]

    await settings_handlers.on_topic_toggle(
        callback, set_cb("topic_toggle", "topics", "work"), state
    )
    assert (await state.get_data())["selected_topics"] == ["music"]


async def test_topics_done_saves_and_clears_state() -> None:
    callback = make_callback()
    state = make_state()
    await state.set_state(settings_handlers.SettingsFlow.topics)
    await state.update_data(selected_topics=["music", "sport"])
    user_service, profile_service = make_services(make_profile())

    await settings_handlers.on_topics_done(
        callback, state, user_service, profile_service
    )

    patch: ProfileUpdate = profile_service.update_profile.await_args.args[1]
    assert [topic.value for topic in patch.topics or []] == ["music", "sport"]
    assert await state.get_state() is None


async def test_custom_timezone_input_applies_patch() -> None:
    state = make_state()
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.text = "екб"
    message.from_user = type("U", (), {"id": 12345})()
    user_service, profile_service = make_services(make_profile())
    await state.set_state(settings_handlers.SettingsFlow.timezone_input)

    await settings_handlers.on_timezone_text(
        message, state, user_service, profile_service
    )

    patch: ProfileUpdate = profile_service.update_profile.await_args.args[1]
    assert patch.timezone == "Asia/Yekaterinburg"
    assert await state.get_state() is None


async def test_custom_timezone_unknown_input_keeps_state() -> None:
    state = make_state()
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.text = "альфа-центавра"
    message.from_user = type("U", (), {"id": 12345})()
    user_service, profile_service = make_services(make_profile())
    await state.set_state(settings_handlers.SettingsFlow.timezone_input)

    await settings_handlers.on_timezone_text(
        message, state, user_service, profile_service
    )

    profile_service.update_profile.assert_not_awaited()
    assert (
        await state.get_state() == settings_handlers.SettingsFlow.timezone_input.state
    )
