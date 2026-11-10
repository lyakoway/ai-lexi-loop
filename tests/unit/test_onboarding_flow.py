"""Tests for the onboarding FSM flow.

Handlers are invoked directly with a real FSMContext backed by in-memory
storage, so the actual state machine transitions and draft contents are
exercised without any Telegram or database access.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import onboarding as onboarding_handlers
from app.bot.keyboards.callbacks import OnboardingCB
from app.db.models import User
from app.repositories import StorageError
from app.schemas.profile import ProfileCreate
from app.services import PlacementService, ProfileService, UserService

TEST_USER = User(id=11)

# The diagnostic service is never invoked by regular answers; a strict
# mock proves it when a test presses a concrete level instead of the
# "unknown level" option.
PLACEMENT_SERVICE = AsyncMock(spec=PlacementService)


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


def make_services(profile: Any = None) -> tuple[Any, Any]:
    user_service = AsyncMock(spec=UserService)
    user_service.ensure_registered.return_value = (TEST_USER, False)
    profile_service = AsyncMock(spec=ProfileService)
    profile_service.get_profile.return_value = profile
    profile_service.build_draft.return_value = {}
    return user_service, profile_service


def cb(action: str, step: str, value: str = "") -> OnboardingCB:
    return OnboardingCB(action=action, step=step, value=value)


def edited_texts(callback: Any) -> list[str]:
    return [call.args[0] for call in callback.message.edit_text.await_args_list]


async def answer_step(callback: Any, state: FSMContext, step: str, value: str) -> None:
    await onboarding_handlers.on_answer(
        callback, cb("ans", step, value), state, PLACEMENT_SERVICE
    )


async def drive_full_flow(callback: Any, state: FSMContext) -> None:
    """Walk the whole flow to the confirmation screen."""
    user_service, profile_service = make_services()
    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )

    await answer_step(callback, state, "native_language", "ru")
    await answer_step(callback, state, "target_language", "en")
    await answer_step(callback, state, "level", "B1")
    await answer_step(callback, state, "learning_goal", "work")
    # Topics: toggle two, then finish the step.
    await onboarding_handlers.on_topic_toggle(
        callback, cb("toggle", "topics", "work"), state
    )
    await onboarding_handlers.on_topic_toggle(
        callback, cb("toggle", "topics", "technology"), state
    )
    await onboarding_handlers.on_topics_done(callback, cb("next", "topics"), state)
    await answer_step(callback, state, "preferred_accent", "uk")
    await answer_step(callback, state, "daily_study_minutes", "15")
    await answer_step(callback, state, "new_items_per_day", "5")
    await answer_step(callback, state, "lesson_time", "08:00")
    await answer_step(callback, state, "review_time", "19:00")
    await answer_step(callback, state, "timezone", "Europe/Moscow")


async def test_full_flow_reaches_confirmation() -> None:
    callback = make_callback()
    state = make_state()

    await drive_full_flow(callback, state)

    assert await state.get_state() == onboarding_handlers.OnboardingFlow.confirm.state
    draft = await state.get_data()
    assert draft == {
        "native_language": "ru",
        "target_language": "en",
        "level": "B1",
        "learning_goal": "work",
        "topics": ["work", "technology"],
        "preferred_accent": "uk",
        "daily_study_minutes": "15",
        "new_items_per_day": "5",
        "lesson_time": "08:00",
        "review_time": "19:00",
        "timezone": "Europe/Moscow",
    }
    confirmation = edited_texts(callback)[-1]
    assert "Проверьте настройки" in confirmation
    assert "работа" in confirmation.lower()


async def test_confirmation_saves_profile_and_clears_state() -> None:
    callback = make_callback()
    state = make_state()
    await drive_full_flow(callback, state)
    user_service, profile_service = make_services()

    await onboarding_handlers.on_confirm(callback, state, user_service, profile_service)

    profile_service.save_profile.assert_awaited_once()
    saved: ProfileCreate = profile_service.save_profile.await_args.args[1]
    assert saved.level.value == "B1"
    assert saved.daily_study_minutes == 15
    assert await state.get_state() is None


async def test_confirmation_failure_keeps_state() -> None:
    """A storage failure must let the user retry the confirmation."""
    callback = make_callback()
    state = make_state()
    await drive_full_flow(callback, state)
    user_service, profile_service = make_services()
    profile_service.save_profile.side_effect = StorageError("db is down")

    await onboarding_handlers.on_confirm(callback, state, user_service, profile_service)

    assert await state.get_state() == onboarding_handlers.OnboardingFlow.confirm.state
    callback.answer.assert_awaited_with(
        "Не удалось сохранить профиль, попробуйте ещё раз", show_alert=True
    )


async def test_stale_answer_button_is_rejected() -> None:
    """A pressed button from an already finished step must not apply."""
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services()
    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )
    await answer_step(callback, state, "native_language", "ru")

    # Old button of the first step pressed while at the second step.
    await answer_step(callback, state, "native_language", "ru")

    callback.answer.assert_awaited_with(texts.ONBOARDING_STALE, show_alert=False)
    draft = await state.get_data()
    assert draft == {"native_language": "ru"}
    assert (
        await state.get_state()
        == onboarding_handlers.OnboardingFlow.target_language.state
    )


async def test_invalid_answer_value_is_rejected() -> None:
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services()
    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )
    await answer_step(callback, state, "native_language", "ru")

    await answer_step(callback, state, "target_language", "klingon")

    callback.answer.assert_awaited_with(texts.ONBOARDING_INVALID_ANSWER)
    assert "target_language" not in await state.get_data()


async def test_back_returns_to_previous_step_keeping_data() -> None:
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services()
    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )
    await answer_step(callback, state, "native_language", "ru")
    await answer_step(callback, state, "target_language", "en")
    await answer_step(callback, state, "level", "B1")
    # Now at the learning_goal step; «Назад» returns to the level step.
    await onboarding_handlers.on_back(callback, cb("back", "learning_goal"), state)

    assert await state.get_state() == onboarding_handlers.OnboardingFlow.level.state
    assert await state.get_data() == {
        "native_language": "ru",
        "target_language": "en",
        "level": "B1",
    }


async def test_back_on_first_step_is_rejected() -> None:
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services()
    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )

    await onboarding_handlers.on_back(callback, cb("back", "native_language"), state)

    callback.answer.assert_awaited_with(texts.ONBOARDING_BACK_UNAVAILABLE)


async def test_skip_optional_and_reject_required() -> None:
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services()
    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )
    await answer_step(callback, state, "native_language", "ru")
    await answer_step(callback, state, "target_language", "en")

    # level is required
    await onboarding_handlers.on_skip(callback, cb("skip", "level"), state)
    callback.answer.assert_awaited_with(texts.ONBOARDING_CANT_SKIP)

    # walk to the accent step and skip it («unknown» now starts the
    # level diagnostic — covered in test_placement_flow; here a
    # concrete level is pressed)
    await answer_step(callback, state, "level", "B1")
    await answer_step(callback, state, "learning_goal", "for_myself")
    await onboarding_handlers.on_topic_toggle(
        callback, cb("toggle", "topics", "sport"), state
    )
    await onboarding_handlers.on_topics_done(callback, cb("next", "topics"), state)
    await onboarding_handlers.on_skip(callback, cb("skip", "preferred_accent"), state)

    draft = await state.get_data()
    assert draft["preferred_accent"] == "us"
    assert draft["topics"] == ["sport"]
    assert (
        await state.get_state()
        == onboarding_handlers.OnboardingFlow.daily_study_minutes.state
    )


async def test_skip_topics_stores_empty_list() -> None:
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services()
    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )
    await answer_step(callback, state, "native_language", "ru")
    await answer_step(callback, state, "target_language", "en")
    await answer_step(callback, state, "level", "A1")
    await answer_step(callback, state, "learning_goal", "travel")

    await onboarding_handlers.on_skip(callback, cb("skip", "topics"), state)

    assert (await state.get_data())["topics"] == []


async def test_cancel_clears_state_without_touching_storage() -> None:
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services()
    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )

    await onboarding_handlers.on_cancel(
        callback, cb("cancel", "native_language"), state
    )

    assert await state.get_state() is None
    assert await state.get_data() == {}
    profile_service.save_profile.assert_not_awaited()
    assert texts.ONBOARDING_CANCELLED in edited_texts(callback)[-1]


async def test_restart_resets_draft() -> None:
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services()
    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )
    await answer_step(callback, state, "native_language", "ru")

    await onboarding_handlers.on_restart(callback, cb("restart", "confirm"), state)

    assert await state.get_data() == {}
    assert (
        await state.get_state()
        == onboarding_handlers.OnboardingFlow.native_language.state
    )


async def test_start_prefills_from_existing_profile() -> None:
    """Re-running onboarding starts from the stored answers."""
    from datetime import date, time

    from app.db.models import Profile

    callback = make_callback()
    state = make_state()
    stored = Profile(
        id=3,
        user_id=TEST_USER.id,
        native_language="ru",
        target_language="en",
        level="B2",
        learning_goal="work",
        topics=["work"],
        preferred_accent="us",
        daily_study_minutes=30,
        new_items_per_day=10,
        lesson_time=time(9, 0),
        review_time=time(20, 0),
        timezone="Asia/Novosibirsk",
        created_at=date(2026, 11, 3),
        updated_at=date(2026, 11, 3),
    )
    draft = {
        "native_language": "ru",
        "target_language": "en",
        "level": "B2",
        "learning_goal": "work",
        "topics": ["work"],
        "preferred_accent": "us",
        "daily_study_minutes": 30,
        "new_items_per_day": 10,
        "lesson_time": "09:00",
        "review_time": "20:00",
        "timezone": "Asia/Novosibirsk",
    }
    user_service, profile_service = make_services(profile=stored)
    profile_service.build_draft.return_value = draft

    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )

    assert await state.get_data() == draft


async def test_timezone_free_text_input_resolves_and_confirms() -> None:
    callback = make_callback()
    state = make_state()
    await drive_full_flow(callback, state)
    # Back to the timezone step, then switch to text input.
    await onboarding_handlers.on_restart(callback, cb("restart", "confirm"), state)
    for step, value in [
        ("native_language", "ru"),
        ("target_language", "en"),
        ("level", "B1"),
        ("learning_goal", "work"),
    ]:
        await answer_step(callback, state, step, value)
    await onboarding_handlers.on_skip(callback, cb("skip", "topics"), state)
    for step, value in [
        ("preferred_accent", "uk"),
        ("daily_study_minutes", "15"),
        ("new_items_per_day", "5"),
        ("lesson_time", "08:00"),
        ("review_time", "19:00"),
    ]:
        await answer_step(callback, state, step, value)

    await onboarding_handlers.on_timezone_other(
        callback, cb("tzother", "timezone"), state
    )
    assert (
        await state.get_state()
        == onboarding_handlers.OnboardingFlow.timezone_input.state
    )

    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.text = "новосибирск"
    await onboarding_handlers.on_timezone_text(message, state)

    draft = await state.get_data()
    assert draft["timezone"] == "Asia/Novosibirsk"
    assert await state.get_state() == onboarding_handlers.OnboardingFlow.confirm.state
    sent = [call.args[0] for call in message.answer.await_args_list]
    assert any("Проверьте настройки" in text for text in sent)


async def test_timezone_free_text_unknown_input_reprompts() -> None:
    state = make_state()
    await state.set_state(onboarding_handlers.OnboardingFlow.timezone_input)

    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.text = "марс"
    await onboarding_handlers.on_timezone_text(message, state)

    message.answer.assert_awaited_once()
    assert (
        await state.get_state()
        == onboarding_handlers.OnboardingFlow.timezone_input.state
    )


async def test_stale_callback_without_flow_answers_politely() -> None:
    callback = make_callback()

    await onboarding_handlers.on_stale_callback(callback)

    callback.answer.assert_awaited_once_with(texts.ONBOARDING_STALE, show_alert=False)


async def test_start_onboarding_storage_failure_answers_apologetically() -> None:
    callback = make_callback()
    state = make_state()
    user_service, profile_service = make_services()
    user_service.ensure_registered.side_effect = StorageError("db is down")

    await onboarding_handlers.start_onboarding(
        callback, state, user_service, profile_service
    )

    callback.message.answer.assert_awaited_once()
    assert await state.get_state() is None
