"""Unit tests for the tutor Telegram handlers (explain + back to card)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import lesson as lesson_handlers
from app.bot.handlers import review as review_handlers
from app.bot.handlers import tutor as tutor_handlers
from app.bot.keyboards.tutor import TutorCB
from app.db.models import Expression, User
from app.repositories import StorageError
from app.services import LessonService, TutorService
from app.services.tutor import TutorExplanation, TutorUnavailable
from tests.unit.test_tutor_service import VALID_JSON, make_expression

TEST_USER = User(id=11)
TEST_TELEGRAM_ID = 12345


def make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=TEST_TELEGRAM_ID)
    return FSMContext(storage=storage, key=key)


async def prepare_lesson_state() -> FSMContext:
    state = make_state()
    await state.set_state(lesson_handlers.LessonFlow.active)
    await state.update_data(
        user_id=TEST_USER.id,
        expression_ids=[41, 42],
        index=0,
        token=41,
        lesson_ref="ref",
        started_at=0.0,
    )
    return state


async def prepare_review_state() -> FSMContext:
    state = make_state()
    await state.set_state(review_handlers.ReviewFlow.active)
    await state.update_data(
        user_id=TEST_USER.id,
        expression_ids=[41, 42],
        index=0,
        token=41,
        remembered_count=0,
        forgot_count=0,
    )
    return state


def make_callback() -> Any:
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    return callback


def make_tutor_service(
    result: TutorExplanation | TutorUnavailable | None = None,
    error: Exception | None = None,
) -> Any:
    service = AsyncMock(spec=TutorService)
    if error is not None:
        service.explain_expression.side_effect = error
    else:
        service.explain_expression.return_value = result
    return service


def make_lesson_service(expressions: list[Expression] | None = None) -> Any:
    service = AsyncMock(spec=LessonService)
    service.get_expressions.return_value = expressions or [
        make_expression(41),
        make_expression(42),
    ]
    return service


def tutor_cb(action: str, token: int, index: int) -> TutorCB:
    return TutorCB(action=action, token=token, index=index)


def make_explanation_result() -> TutorExplanation:
    from app.services.tutor.outputs import ExplanationOut

    explanation = ExplanationOut.model_validate_json(VALID_JSON)
    return TutorExplanation(
        explanation=explanation,
        expression=make_expression(41),
        prompt_version="explain_v1",
    )


async def test_explain_success_edits_message_and_keeps_state() -> None:
    state = await prepare_lesson_state()
    callback = make_callback()
    tutor_service = make_tutor_service(make_explanation_result())
    lesson_service = make_lesson_service()

    await tutor_handlers.on_explain(
        callback, tutor_cb("explain", 41, 0), state, tutor_service, lesson_service
    )

    tutor_service.explain_expression.assert_awaited_once_with(TEST_USER.id, 41)
    edited = callback.message.edit_text.await_args.args[0]
    assert "Break a leg!" in edited
    assert texts.TUTOR_AI_NOTE in edited
    # The lesson flow stays intact: same state and position.
    data = await state.get_data()
    assert data["index"] == 0
    assert await state.get_state() == lesson_handlers.LessonFlow.active.state


async def test_explain_unavailable_shows_fallback_with_back() -> None:
    state = await prepare_lesson_state()
    callback = make_callback()
    tutor_service = make_tutor_service(TutorUnavailable(reason="llm_error"))
    lesson_service = make_lesson_service()

    await tutor_handlers.on_explain(
        callback, tutor_cb("explain", 41, 0), state, tutor_service, lesson_service
    )

    edited = callback.message.edit_text.await_args
    assert edited.args[0] == texts.TUTOR_UNAVAILABLE
    assert "tut~back~41~0" in str(edited.kwargs)


async def test_explain_storage_error_shows_fallback() -> None:
    state = await prepare_lesson_state()
    callback = make_callback()
    tutor_service = make_tutor_service(error=StorageError("db down"))
    lesson_service = make_lesson_service()

    await tutor_handlers.on_explain(
        callback, tutor_cb("explain", 41, 0), state, tutor_service, lesson_service
    )

    assert callback.message.edit_text.await_args.args[0] == texts.TUTOR_UNAVAILABLE


async def test_explain_from_wrong_card_is_stale() -> None:
    state = await prepare_lesson_state()
    callback = make_callback()
    tutor_service = make_tutor_service(make_explanation_result())
    lesson_service = make_lesson_service()

    await tutor_handlers.on_explain(
        callback, tutor_cb("explain", 999, 0), state, tutor_service, lesson_service
    )

    tutor_service.explain_expression.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.TUTOR_STALE, show_alert=False)


async def test_explain_without_flow_is_stale() -> None:
    state = make_state()
    callback = make_callback()
    tutor_service = make_tutor_service(make_explanation_result())
    lesson_service = make_lesson_service()

    await tutor_handlers.on_explain(
        callback, tutor_cb("explain", 41, 0), state, tutor_service, lesson_service
    )

    tutor_service.explain_expression.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.TUTOR_STALE, show_alert=False)


async def test_back_in_lesson_restores_card_keyboard() -> None:
    state = await prepare_lesson_state()
    callback = make_callback()
    lesson_service = make_lesson_service()

    await tutor_handlers.on_back_to_card(
        callback, tutor_cb("back", 41, 0), state, lesson_service
    )

    edited = callback.message.edit_text.await_args
    assert "Break a leg!" in edited.args[0]
    assert "tut~explain~41~0" in str(edited.kwargs)
    assert "lssn~next~41~0" in str(edited.kwargs)


async def test_back_in_review_restores_review_keyboard() -> None:
    state = await prepare_review_state()
    callback = make_callback()
    lesson_service = make_lesson_service()

    await tutor_handlers.on_back_to_card(
        callback, tutor_cb("back", 41, 0), state, lesson_service
    )

    edited = callback.message.edit_text.await_args
    assert texts.REVIEW_PREFIX in edited.args[0]
    assert "rvw~remember~41~0" in str(edited.kwargs)


async def test_back_with_stale_button_is_rejected() -> None:
    state = await prepare_lesson_state()
    await state.update_data(index=1)  # card moved on
    callback = make_callback()
    lesson_service = make_lesson_service()

    await tutor_handlers.on_back_to_card(
        callback, tutor_cb("back", 41, 0), state, lesson_service
    )

    lesson_service.get_expressions.assert_not_awaited()
    callback.answer.assert_awaited_once_with(texts.TUTOR_STALE, show_alert=False)


async def test_lesson_and_review_continue_after_explanation() -> None:
    """A failed explanation must not disturb the lesson flow state."""
    state = await prepare_lesson_state()
    callback = make_callback()
    tutor_service = make_tutor_service(TutorUnavailable(reason="llm_disabled"))
    lesson_service = make_lesson_service()

    await tutor_handlers.on_explain(
        callback, tutor_cb("explain", 41, 0), state, tutor_service, lesson_service
    )

    data = await state.get_data()
    assert data["expression_ids"] == [41, 42]
    assert data["token"] == 41
    assert await state.get_state() == lesson_handlers.LessonFlow.active.state


async def test_repeated_explain_reuses_cache_without_new_llm_call() -> None:
    """A second press of the same button must not pay for another call."""
    state = await prepare_lesson_state()
    callback = make_callback()
    tutor_service = make_tutor_service(make_explanation_result())
    lesson_service = make_lesson_service()

    await tutor_handlers.on_explain(
        callback, tutor_cb("explain", 41, 0), state, tutor_service, lesson_service
    )
    assert tutor_service.explain_expression.await_count == 1

    # Same button pressed again (e.g. after returning to the card).
    callback2 = make_callback()
    await tutor_handlers.on_explain(
        callback2, tutor_cb("explain", 41, 0), state, tutor_service, lesson_service
    )

    assert tutor_service.explain_expression.await_count == 1  # no second call
    assert callback2.message.edit_text.await_args.args[0] == (
        callback.message.edit_text.await_args.args[0]
    )


async def test_cache_does_not_leak_across_cards() -> None:
    """A cached view for card 0 must not be shown for card 1."""
    state = await prepare_lesson_state()
    callback = make_callback()
    tutor_service = make_tutor_service(make_explanation_result())
    lesson_service = make_lesson_service()

    await tutor_handlers.on_explain(
        callback, tutor_cb("explain", 41, 0), state, tutor_service, lesson_service
    )
    # Move to the next card, then press the old cached button.
    await state.update_data(index=1)
    callback2 = make_callback()
    tutor_service2 = make_tutor_service(make_explanation_result())

    await tutor_handlers.on_explain(
        callback2, tutor_cb("explain", 41, 1), state, tutor_service2, lesson_service
    )

    assert tutor_service2.explain_expression.await_count == 1
