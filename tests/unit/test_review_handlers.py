"""Tests for the /review command and answer callbacks."""

from datetime import UTC, date, time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import review as review_handlers
from app.bot.keyboards.review import ReviewCB, review_card_keyboard
from app.db.models import Expression, ExpressionProgress, Profile, User
from app.domain.srs import SrsState, SrsUpdate
from app.repositories import StorageError
from app.services import ProfileService, ReviewService, UserService
from app.services.reviews import ReviewPlan

TEST_USER = User(id=11)
TEST_TELEGRAM_ID = 12345

ANSWER_TIME = SimpleNamespace(answered_at=0)  # only marks call shape


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
        new_items_per_day=5,
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
    review_service = AsyncMock(spec=ReviewService)
    return user_service, profile_service, review_service


def make_plan() -> ReviewPlan:
    expressions = [make_expression(31), make_expression(32, "Piece of cake")]
    rows = [
        ExpressionProgress(
            id=number,
            user_id=TEST_USER.id,
            expression_id=expression.id,
        )
        for number, expression in enumerate(expressions, start=1)
    ]
    return ReviewPlan(progress_rows=rows, expressions=expressions)


def srs_update(review_count: int) -> SrsUpdate:
    from datetime import datetime, timedelta

    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    return SrsUpdate(
        ease_factor=2.5,
        interval_days=review_count,
        review_count=review_count,
        answered_at=now,
        next_review_at=now + timedelta(days=review_count),
    )


def rvw_cb(action: str, token: int, index: int) -> ReviewCB:
    return ReviewCB(action=action, token=token, index=index)


async def start_review(state: FSMContext, review_service: Any) -> Any:
    review_service.build_review.return_value = make_plan()
    user_service, profile_service, _ = make_services(profile=make_profile())
    message = make_message()
    await review_handlers.cmd_review(
        message, state, user_service, profile_service, review_service
    )
    return message


async def test_review_without_profile_invites_onboarding() -> None:
    state = make_state()
    message = make_message()
    user_service, profile_service, review_service = make_services(profile=None)

    await review_handlers.cmd_review(
        message, state, user_service, profile_service, review_service
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args[0] == texts.PROFILE_EMPTY
    review_service.build_review.assert_not_awaited()


async def test_review_sends_first_card_and_sets_state() -> None:
    state = make_state()
    review_service = AsyncMock(spec=ReviewService)

    message = await start_review(state, review_service)

    message.answer.assert_awaited_once()
    card = message.answer.await_args.args[0]
    assert card.startswith(texts.REVIEW_PREFIX)
    assert "Break a leg!" in card
    assert "Карточка 1 из 2" in card
    assert await state.get_state() == review_handlers.ReviewFlow.active.state
    data = await state.get_data()
    assert data["expression_ids"] == [31, 32]
    assert data["index"] == 0
    assert data["token"] == 31
    assert data["user_id"] == TEST_USER.id
    assert data["remembered_count"] == 0
    assert data["forgot_count"] == 0


async def test_review_without_due_cards() -> None:
    from app.services.reviews import ReviewUnavailable

    state = make_state()
    message = make_message()
    user_service, profile_service, review_service = make_services(
        profile=make_profile()
    )
    review_service.build_review.return_value = ReviewUnavailable(
        reason="no_due_reviews"
    )

    await review_handlers.cmd_review(
        message, state, user_service, profile_service, review_service
    )

    assert message.answer.await_args.args[0] == texts.REVIEW_NO_DUE
    assert await state.get_state() is None


async def test_review_build_failure_is_reported() -> None:
    state = make_state()
    message = make_message()
    user_service, profile_service, review_service = make_services(
        profile=make_profile()
    )
    review_service.build_review.side_effect = StorageError("db down")

    await review_handlers.cmd_review(
        message, state, user_service, profile_service, review_service
    )

    assert message.answer.await_args.args[0] == texts.REVIEW_BUILD_FAILED


async def test_remember_answer_advances_to_next_card() -> None:
    state = make_state()
    review_service = AsyncMock(spec=ReviewService)
    await start_review(state, review_service)
    review_service.apply_answer.return_value = srs_update(1)
    review_service.get_expressions.return_value = make_plan().expressions
    callback = make_callback()

    await review_handlers.on_answer(
        callback, rvw_cb("remember", token=31, index=0), state, review_service
    )

    review_service.apply_answer.assert_awaited_once_with(
        TEST_USER.id, 31, remembered=True
    )
    callback.message.edit_text.assert_awaited_once()
    card = callback.message.edit_text.await_args.args[0]
    assert "Piece of cake" in card
    assert "Карточка 2 из 2" in card
    data = await state.get_data()
    assert data["index"] == 1
    assert data["remembered_count"] == 1
    assert data["forgot_count"] == 0


async def test_forgot_answer_counts_and_advances() -> None:
    state = make_state()
    review_service = AsyncMock(spec=ReviewService)
    await start_review(state, review_service)
    review_service.apply_answer.return_value = srs_update(0)
    review_service.get_expressions.return_value = make_plan().expressions
    callback = make_callback()

    await review_handlers.on_answer(
        callback, rvw_cb("forgot", token=31, index=0), state, review_service
    )

    review_service.apply_answer.assert_awaited_once_with(
        TEST_USER.id, 31, remembered=False
    )
    data = await state.get_data()
    assert data["index"] == 1
    assert data["remembered_count"] == 0
    assert data["forgot_count"] == 1


async def test_last_answer_shows_summary_and_clears_state() -> None:
    state = make_state()
    review_service = AsyncMock(spec=ReviewService)
    await start_review(state, review_service)
    await state.update_data(index=1, remembered_count=1)
    review_service.apply_answer.return_value = srs_update(2)
    callback = make_callback()

    await review_handlers.on_answer(
        callback, rvw_cb("remember", token=31, index=1), state, review_service
    )

    assert await state.get_state() is None
    summary = callback.message.edit_text.await_args.args[0]
    assert "Помню: 2 · Забыл: 0" in summary


async def test_double_press_is_rejected_without_second_apply() -> None:
    """The second press of an already-answered card must not reapply."""
    state = make_state()
    review_service = AsyncMock(spec=ReviewService)
    await start_review(state, review_service)
    review_service.apply_answer.return_value = srs_update(1)
    review_service.get_expressions.return_value = make_plan().expressions
    callback = make_callback()

    first = rvw_cb("remember", token=31, index=0)
    await review_handlers.on_answer(callback, first, state, review_service)
    second = rvw_cb("remember", token=31, index=0)
    await review_handlers.on_answer(callback, second, state, review_service)

    review_service.apply_answer.assert_awaited_once()
    callback.answer.assert_awaited_with(texts.REVIEW_STALE, show_alert=False)


async def test_wrong_token_is_rejected() -> None:
    state = make_state()
    review_service = AsyncMock(spec=ReviewService)
    await start_review(state, review_service)
    callback = make_callback()

    await review_handlers.on_answer(
        callback, rvw_cb("remember", token=999, index=0), state, review_service
    )

    review_service.apply_answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.REVIEW_STALE, show_alert=False)


async def test_answer_without_session_is_stale() -> None:
    state = make_state()
    callback = make_callback()
    review_service = AsyncMock(spec=ReviewService)

    await review_handlers.on_answer(
        callback, rvw_cb("remember", token=31, index=0), state, review_service
    )

    review_service.apply_answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.REVIEW_STALE, show_alert=False)


async def test_answer_db_error_keeps_card_for_retry() -> None:
    """A failed save must not look like success or advance the card."""
    state = make_state()
    review_service = AsyncMock(spec=ReviewService)
    await start_review(state, review_service)
    review_service.apply_answer.side_effect = StorageError("db down")
    callback = make_callback()

    await review_handlers.on_answer(
        callback, rvw_cb("remember", token=31, index=0), state, review_service
    )

    callback.answer.assert_awaited_once_with(
        texts.REVIEW_ANSWER_FAILED, show_alert=True
    )
    assert (await state.get_data())["index"] == 0


async def test_answer_for_missing_progress_drops_session() -> None:
    state = make_state()
    review_service = AsyncMock(spec=ReviewService)
    await start_review(state, review_service)
    review_service.apply_answer.return_value = None
    callback = make_callback()

    await review_handlers.on_answer(
        callback, rvw_cb("remember", token=31, index=0), state, review_service
    )

    assert await state.get_state() is None
    callback.answer.assert_awaited_with(texts.REVIEW_STALE, show_alert=True)


async def test_stale_callback_without_session() -> None:
    callback = make_callback()

    await review_handlers.on_stale_review_callback(callback)

    callback.answer.assert_awaited_once_with(texts.REVIEW_STALE, show_alert=False)


def test_review_keyboard_callbacks() -> None:
    keyboard = review_card_keyboard(token=31, index=0)
    callbacks = [
        button.callback_data for row in keyboard.inline_keyboard for button in row
    ]
    assert "rvw~remember~31~0" in callbacks
    assert "rvw~forgot~31~0" in callbacks
    unpacked = ReviewCB.unpack("rvw~remember~31~0")
    assert unpacked.action == "remember"
    assert unpacked.token == 31
    assert unpacked.index == 0


def test_srs_state_defaults_for_new_cards() -> None:
    state = SrsState()
    assert (state.ease_factor, state.interval_days, state.review_count) == (
        2.5,
        1,
        0,
    )
