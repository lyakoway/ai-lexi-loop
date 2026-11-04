"""Tests for the /lesson command and lesson navigation callbacks."""

from datetime import date, time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import lesson as lesson_handlers
from app.bot.keyboards.lesson import LessonCB
from app.db.models import Expression, Profile, User
from app.repositories import StorageError
from app.services import LessonService, ProfileService, UserService

TEST_USER = User(id=11)
TEST_TELEGRAM_ID = 12345


def make_expression(expression_id: int, text: str = "Break a leg!") -> Expression:
    return Expression(
        id=expression_id,
        language="en",
        text=text,
        translation="Ни пуха ни пера!",
        ipa="breɪk ə lɛɡ",
        phonetic_hint_ru="брЭйк э лЭг",
        example="You have an exam tomorrow? Break a leg!",
        example_translation="Завтра экзамен? Ни пуха ни пера!",
        level="B1",
        topic="everyday",
    )


def make_profile() -> Profile:
    return Profile(
        id=1,
        user_id=TEST_USER.id,
        native_language="ru",
        target_language="en",
        level="B1",
        learning_goal="work",
        topics=["everyday"],
        preferred_accent="uk",
        daily_study_minutes=15,
        new_items_per_day=2,
        lesson_time=time(8, 0),
        review_time=time(19, 0),
        timezone="Europe/Moscow",
        created_at=date(2026, 11, 3),
        updated_at=date(2026, 11, 3),
    )


def make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=TEST_TELEGRAM_ID)
    return FSMContext(storage=storage, key=key)


def make_message() -> Any:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.edit_text = AsyncMock()
    message.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    return message


def make_callback() -> Any:
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    return callback


def make_services(
    profile: Profile | None = None,
) -> tuple[Any, Any, Any]:
    user_service = AsyncMock(spec=UserService)
    user_service.ensure_registered.return_value = (TEST_USER, False)
    profile_service = AsyncMock(spec=ProfileService)
    profile_service.get_profile.return_value = profile
    lesson_service = AsyncMock(spec=LessonService)
    return user_service, profile_service, lesson_service


def lesson_cb(action: str, token: int, index: int) -> LessonCB:
    return LessonCB(action=action, token=token, index=index)


async def start_lesson(
    state: FSMContext,
    lesson_service: Any,
    message: Any | None = None,
) -> Any:
    """Run /lesson with a two-expression plan; return the message mock."""
    plan = SimpleNamespace(
        expressions=[make_expression(41), make_expression(42)],
        used_default_level=False,
        lesson_ref="ref123",
    )
    lesson_service.build_lesson.return_value = plan
    lesson_service.get_expressions.return_value = plan.expressions
    user_service, profile_service, _ = make_services(profile=make_profile())
    message = message or make_message()
    await lesson_handlers.cmd_lesson(
        message, state, user_service, profile_service, lesson_service
    )
    return message


async def test_lesson_without_profile_invites_onboarding() -> None:
    message = make_message()
    user_service, profile_service, lesson_service = make_services(profile=None)

    await lesson_handlers.cmd_lesson(
        message, make_state(), user_service, profile_service, lesson_service
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == texts.PROFILE_EMPTY
    lesson_service.build_lesson.assert_not_awaited()


async def test_lesson_sends_first_card_and_sets_state() -> None:
    state = make_state()
    message = make_message()
    user_service, profile_service, lesson_service = make_services(
        profile=make_profile()
    )
    lesson_service.build_lesson.return_value = SimpleNamespace(
        expressions=[make_expression(41), make_expression(42)],
        used_default_level=False,
        lesson_ref="ref123",
    )

    await lesson_handlers.cmd_lesson(
        message, state, user_service, profile_service, lesson_service
    )

    message.answer.assert_awaited_once()
    card = message.answer.await_args.args[0]
    for fragment in (
        "Break a leg!",
        "/breɪk ə lɛɡ/",
        "брЭйк э лЭг",
        "Ни пуха ни пера!",
        "You have an exam tomorrow?",
        "Ни пуха ни пера",
        texts.PHONETIC_DISCLAIMER,
        "Карточка 1 из 2",
    ):
        assert fragment in card, f"card must contain {fragment!r}"
    assert await state.get_state() == lesson_handlers.LessonFlow.active.state
    data = await state.get_data()
    assert data["expression_ids"] == [41, 42]
    assert data["index"] == 0
    assert data["token"] == 41
    assert data["user_id"] == TEST_USER.id


async def test_lesson_notes_default_level() -> None:
    state = make_state()
    message = make_message()
    user_service, profile_service, lesson_service = make_services(
        profile=make_profile()
    )
    lesson_service.build_lesson.return_value = SimpleNamespace(
        expressions=[make_expression(41)],
        used_default_level=True,
        lesson_ref="ref123",
    )

    await lesson_handlers.cmd_lesson(
        message, state, user_service, profile_service, lesson_service
    )

    card = message.answer.await_args.args[0]
    assert card.startswith(texts.LESSON_DEFAULT_LEVEL_NOTE)


async def test_lesson_unavailable_messages_by_reason() -> None:
    from app.services.lessons import LessonUnavailable

    for reason in (
        "no_expressions_for_topics",
        "no_expressions_for_level",
        "all_served",
    ):
        state = make_state()
        message = make_message()
        user_service, profile_service, lesson_service = make_services(
            profile=make_profile()
        )
        lesson_service.build_lesson.return_value = LessonUnavailable(reason=reason)

        await lesson_handlers.cmd_lesson(
            message, state, user_service, profile_service, lesson_service
        )

        sent = message.answer.await_args.args[0]
        assert sent == texts.LESSON_UNAVAILABLE_MESSAGES[reason].format(level="B1")
        assert await state.get_state() is None


async def test_lesson_build_failure_is_reported() -> None:
    state = make_state()
    message = make_message()
    user_service, profile_service, lesson_service = make_services(
        profile=make_profile()
    )
    lesson_service.build_lesson.side_effect = StorageError("db down")

    await lesson_handlers.cmd_lesson(
        message, state, user_service, profile_service, lesson_service
    )

    assert message.answer.await_args.args[0] == texts.LESSON_BUILD_FAILED
    assert await state.get_state() is None


async def test_next_card_advances_and_edits() -> None:
    state = make_state()
    message = await start_lesson(state, AsyncMock(spec=LessonService))
    callback = make_callback()
    callback.message = message
    lesson_service = AsyncMock(spec=LessonService)
    lesson_service.get_expressions.return_value = [
        make_expression(41),
        make_expression(42, "It's a piece of cake"),
    ]

    await lesson_handlers.on_next_card(
        callback, lesson_cb("next", token=41, index=0), state, lesson_service
    )

    callback.message.edit_text.assert_awaited_once()
    card = callback.message.edit_text.await_args.args[0]
    assert "It's a piece of cake" in card
    assert "Карточка 2 из 2" in card
    assert (await state.get_data())["index"] == 1


async def test_next_card_on_last_card_is_stale() -> None:
    state = make_state()
    await start_lesson(state, AsyncMock(spec=LessonService))
    await state.update_data(index=1)  # standing on the last card
    callback = make_callback()
    lesson_service = AsyncMock(spec=LessonService)

    await lesson_handlers.on_next_card(
        callback, lesson_cb("next", token=41, index=1), state, lesson_service
    )

    callback.answer.assert_awaited_once_with(texts.LESSON_STALE, show_alert=False)
    lesson_service.get_expressions.assert_not_awaited()


async def test_next_card_with_wrong_token_is_stale() -> None:
    """A button from an old lesson must not affect the current one."""
    state = make_state()
    await start_lesson(state, AsyncMock(spec=LessonService))
    callback = make_callback()
    lesson_service = AsyncMock(spec=LessonService)

    await lesson_handlers.on_next_card(
        callback, lesson_cb("next", token=999, index=0), state, lesson_service
    )

    callback.answer.assert_awaited_once_with(texts.LESSON_STALE, show_alert=False)
    assert (await state.get_data())["index"] == 0


async def test_double_press_of_next_is_rejected() -> None:
    """The second press of an already-used button is stale."""
    state = make_state()
    await start_lesson(state, AsyncMock(spec=LessonService))
    callback = make_callback()
    lesson_service = AsyncMock(spec=LessonService)
    lesson_service.get_expressions.return_value = [
        make_expression(41),
        make_expression(42),
    ]
    first = lesson_cb("next", token=41, index=0)
    await lesson_handlers.on_next_card(callback, first, state, lesson_service)
    # Same (token=41, index=0) button pressed again — state is at index 1.
    second = lesson_cb("next", token=41, index=0)
    await lesson_handlers.on_next_card(callback, second, state, lesson_service)

    callback.answer.assert_awaited_with(texts.LESSON_STALE, show_alert=False)
    assert (await state.get_data())["index"] == 1


async def test_finish_lesson_marks_progress_and_clears_state() -> None:
    state = make_state()
    await start_lesson(state, AsyncMock(spec=LessonService))
    await state.update_data(index=1)
    callback = make_callback()
    lesson_service = AsyncMock(spec=LessonService)
    lesson_service.finish_lesson.return_value = 2

    await lesson_handlers.on_finish_lesson(
        callback, lesson_cb("finish", token=41, index=1), state, lesson_service
    )

    lesson_service.finish_lesson.assert_awaited_once()
    call = lesson_service.finish_lesson.await_args
    assert call.args == (11, [41, 42])
    assert call.kwargs["source"] == "telegram"
    assert await state.get_state() is None
    summary = callback.message.edit_text.await_args.args[0]
    assert "2" in summary


async def test_finish_from_first_card_counts_only_shown() -> None:
    state = make_state()
    await start_lesson(state, AsyncMock(spec=LessonService))
    callback = make_callback()
    lesson_service = AsyncMock(spec=LessonService)
    lesson_service.finish_lesson.return_value = 1

    await lesson_handlers.on_finish_lesson(
        callback, lesson_cb("finish", token=41, index=0), state, lesson_service
    )

    lesson_service.finish_lesson.assert_awaited_once()
    assert lesson_service.finish_lesson.await_args.args == (11, [41])


async def test_finish_failure_keeps_state_for_retry() -> None:
    state = make_state()
    await start_lesson(state, AsyncMock(spec=LessonService))
    callback = make_callback()
    lesson_service = AsyncMock(spec=LessonService)
    lesson_service.finish_lesson.side_effect = StorageError("db down")

    await lesson_handlers.on_finish_lesson(
        callback, lesson_cb("finish", token=41, index=0), state, lesson_service
    )

    callback.answer.assert_awaited_once_with(texts.LESSON_SAVE_FAILED, show_alert=True)
    assert await state.get_state() == lesson_handlers.LessonFlow.active.state


async def test_finish_with_stale_button_is_rejected() -> None:
    state = make_state()
    await start_lesson(state, AsyncMock(spec=LessonService))
    callback = make_callback()
    lesson_service = AsyncMock(spec=LessonService)

    await lesson_handlers.on_finish_lesson(
        callback, lesson_cb("finish", token=41, index=1), state, lesson_service
    )

    lesson_service.finish_lesson.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.LESSON_STALE, show_alert=False)


async def test_stale_callback_without_active_lesson() -> None:
    """Lesson buttons pressed after a restart (no state) are answered."""
    callback = make_callback()

    await lesson_handlers.on_stale_lesson_callback(callback)

    callback.answer.assert_awaited_once_with(texts.LESSON_STALE, show_alert=False)


async def test_lesson_storage_error_on_user_load_is_reported() -> None:
    state = make_state()
    message = make_message()
    user_service, profile_service, lesson_service = make_services(
        profile=make_profile()
    )
    user_service.ensure_registered.side_effect = StorageError("db down")

    await lesson_handlers.cmd_lesson(
        message, state, user_service, profile_service, lesson_service
    )

    assert message.answer.await_args.args[0] == texts.LESSON_BUILD_FAILED
