"""Unit tests for the /quiz and /forgotten Telegram flow (Stage 6.4.6).

Handlers are called with a mocked QuizService — no real LLM, no
database. The friendly-text mapping renders strictly from
``texts.QUIZ_ERROR_TEXTS`` (parametrized over every reason).
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from app.bot import texts
from app.bot.handlers import quiz as quiz_handlers
from app.bot.handlers.quiz import QuizFlow
from app.bot.keyboards.quiz import QuizCB
from app.domain.srs import utc_now
from app.services import QuizService
from app.services.quiz import (
    ForgottenItem,
    ForgottenReport,
    ForgottenThrottled,
    QuizGradeResult,
    QuizQuestion,
    QuizUnavailable,
)

TEST_TELEGRAM_ID = 12345


def make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=TEST_TELEGRAM_ID)
    return FSMContext(storage=storage, key=key)


def make_message(text: str | None = "break a leg") -> Any:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.text = text
    message.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    return message


def make_user_services() -> tuple[Any, Any]:
    user_service = AsyncMock()
    user_service.ensure_registered.return_value = (
        SimpleNamespace(id=11),
        None,
    )
    profile_service = AsyncMock()
    profile_service.get_profile.return_value = SimpleNamespace(id=1, level="A1")
    return user_service, profile_service


def make_service() -> Any:
    return AsyncMock(spec=QuizService)


def make_question() -> QuizQuestion:
    return QuizQuestion(
        expression_id=42,
        question_translation="сломать ногу (идиомат.)",
        level="A2",
    )


def make_grade(**overrides: Any) -> QuizGradeResult:
    values: dict[str, Any] = {
        "is_correct": True,
        "error_type": "none",
        "explanation": "Смысл передан верно.",
        "corrected": "",
        "level_offer": False,
        "streak": 1,
        "srs": None,
    }
    values.update(overrides)
    return QuizGradeResult(**values)


# ---------------------------------------------------------------------------
# /quiz — serving the question
# ---------------------------------------------------------------------------


async def test_cmd_quiz_shows_translation_and_sets_state() -> None:
    state = make_state()
    message = make_message("/quiz")
    user_service, profile_service = make_user_services()
    service = make_service()
    service.start_quiz.return_value = make_question()

    await quiz_handlers.cmd_quiz(message, state, user_service, profile_service, service)

    service.start_quiz.assert_awaited_once_with(11)
    rendered = message.answer.await_args.args[0]
    assert "сломать ногу" in rendered
    assert "Break a leg" not in rendered  # the answer is never shown
    assert await state.get_state() == QuizFlow.waiting_answer.state
    assert (await state.get_data())["expression_id"] == 42


async def test_cmd_quiz_no_words_shows_friendly_text() -> None:
    state = make_state()
    message = make_message("/quiz")
    user_service, profile_service = make_user_services()
    service = make_service()
    service.start_quiz.return_value = QuizUnavailable(reason="no_words")

    await quiz_handlers.cmd_quiz(message, state, user_service, profile_service, service)

    assert message.answer.await_args.args[0] == texts.QUIZ_NO_WORDS
    assert await state.get_state() is None


# ---------------------------------------------------------------------------
# Answer grading
# ---------------------------------------------------------------------------


async def test_correct_answer_renders_verdict() -> None:
    state = make_state()
    await state.set_state(QuizFlow.waiting_answer)
    await state.update_data(user_id=11, expression_id=42)
    service = make_service()
    service.grade_answer.return_value = make_grade()
    message = make_message("break your leg")

    await quiz_handlers.on_quiz_answer(message, state, service)

    service.grade_answer.assert_awaited_once_with(11, 42, "break your leg")
    rendered = message.answer.await_args.args[0]
    assert texts.QUIZ_CORRECT in rendered
    assert "Смысл передан верно." in rendered
    assert await state.get_state() is None  # one question per session


async def test_wrong_answer_renders_correction() -> None:
    state = make_state()
    await state.set_state(QuizFlow.waiting_answer)
    await state.update_data(user_id=11, expression_id=42)
    service = make_service()
    service.grade_answer.return_value = make_grade(
        is_correct=False,
        error_type="grammar",
        explanation="Нужно break a leg.",
        corrected="Break a leg",
    )
    message = make_message("I break leg")

    await quiz_handlers.on_quiz_answer(message, state, service)

    rendered = message.answer.await_args.args[0]
    assert texts.QUIZ_WRONG in rendered
    assert "Break a leg" in rendered
    assert "Нужно break a leg." in rendered


async def test_level_offer_renders_confirmation_button() -> None:
    state = make_state()
    await state.set_state(QuizFlow.waiting_answer)
    await state.update_data(user_id=11, expression_id=42)
    service = make_service()
    service.grade_answer.return_value = make_grade(level_offer=True, streak=3)
    message = make_message("nailing it")

    await quiz_handlers.on_quiz_answer(message, state, service)

    keyboard = message.answer.await_args.kwargs.get("reply_markup")
    assert keyboard is not None
    packed = keyboard.inline_keyboard[0][0].callback_data
    assert QuizCB.unpack(packed).action == "level_up"
    assert "3 верных ответов" in message.answer.await_args.args[0]


async def test_invalid_output_shows_retry_and_quiz_survives() -> None:
    state = make_state()
    await state.set_state(QuizFlow.waiting_answer)
    await state.update_data(user_id=11, expression_id=42)
    service = make_service()
    service.grade_answer.return_value = QuizUnavailable(reason="invalid_output")
    message = make_message("whatever")

    await quiz_handlers.on_quiz_answer(message, state, service)

    assert message.answer.await_args.args[0] == texts.QUIZ_EVALUATION_RETRY
    assert await state.get_state() is None


async def test_llm_error_maps_to_unavailable_text() -> None:
    state = make_state()
    await state.set_state(QuizFlow.waiting_answer)
    await state.update_data(user_id=11, expression_id=42)
    service = make_service()
    service.grade_answer.return_value = QuizUnavailable(reason="llm_error")
    message = make_message("anything")

    await quiz_handlers.on_quiz_answer(message, state, service)

    assert message.answer.await_args.args[0] == texts.QUIZ_UNAVAILABLE


async def test_command_text_exits_quiz_without_grading() -> None:
    state = make_state()
    await state.set_state(QuizFlow.waiting_answer)
    await state.update_data(user_id=11, expression_id=42)
    service = make_service()
    message = make_message("/cancel")

    await quiz_handlers.on_quiz_answer(message, state, service)

    service.grade_answer.assert_not_awaited()
    assert message.answer.await_args.args[0] == texts.QUIZ_EXITED


async def test_answer_without_state_data_is_stale() -> None:
    state = make_state()
    await state.set_state(QuizFlow.waiting_answer)
    service = make_service()
    message = make_message("hello")

    await quiz_handlers.on_quiz_answer(message, state, service)

    service.grade_answer.assert_not_awaited()
    assert message.answer.await_args.args[0] == texts.QUIZ_STALE


# ---------------------------------------------------------------------------
# Friendly texts: parametrized, no reason leakage
# ---------------------------------------------------------------------------


async def test_every_quiz_reason_has_friendly_text() -> None:
    for reason in texts.QUIZ_ERROR_TEXTS:
        friendly = quiz_handlers._friendly_text(reason)
        assert friendly
        assert reason not in friendly
        assert friendly in texts.QUIZ_ERROR_TEXTS.values()


async def test_unknown_reason_falls_back_to_generic() -> None:
    assert quiz_handlers._friendly_text("mystery_reason") == texts.QUIZ_UNAVAILABLE


# ---------------------------------------------------------------------------
# Level-up confirmation
# ---------------------------------------------------------------------------


async def test_level_up_confirm_renders_new_level() -> None:
    user_service, profile_service = make_user_services()
    service = make_service()
    service.confirm_level_up.return_value = "A2"
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()

    await quiz_handlers.on_level_up_confirm(
        callback, user_service, profile_service, service
    )

    rendered = callback.message.answer.await_args.args[0]
    assert "A2" in rendered


async def test_level_up_declined_when_streak_reset() -> None:
    user_service, profile_service = make_user_services()
    service = make_service()
    service.confirm_level_up.return_value = None
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()

    await quiz_handlers.on_level_up_confirm(
        callback, user_service, profile_service, service
    )

    rendered = callback.message.answer.await_args.args[0]
    assert rendered == texts.QUIZ_LEVEL_UP_DECLINED


async def test_level_up_callback_round_trip() -> None:
    packed = QuizCB(action="level_up").pack()
    assert QuizCB.unpack(packed).action == "level_up"


# ---------------------------------------------------------------------------
# /forgotten
# ---------------------------------------------------------------------------


async def test_forgotten_renders_items() -> None:
    message = make_message("/forgotten")
    user_service, profile_service = make_user_services()
    service = make_service()
    service.forgotten_report.return_value = ForgottenReport(
        items=[
            ForgottenItem(
                text="Break a leg",
                translation="удачи",
                lapse_count=3,
                interval_days=1,
            ),
            ForgottenItem(
                text="Haste makes waste",
                translation="поспешишь — людей насмешишь",
                lapse_count=1,
                interval_days=2,
            ),
        ]
    )

    await quiz_handlers.cmd_forgotten(message, user_service, profile_service, service)

    rendered = message.answer.await_args.args[0]
    assert "Break a leg" in rendered
    assert "провалов: 3" in rendered
    assert "Haste makes waste" in rendered


async def test_forgotten_empty_renders_empty_text() -> None:
    message = make_message("/forgotten")
    user_service, profile_service = make_user_services()
    service = make_service()
    service.forgotten_report.return_value = ForgottenReport(items=[])

    await quiz_handlers.cmd_forgotten(message, user_service, profile_service, service)

    assert message.answer.await_args.args[0] == texts.FORGOTTEN_EMPTY


async def test_forgotten_throttled_renders_next_date() -> None:
    message = make_message("/forgotten")
    user_service, profile_service = make_user_services()
    service = make_service()
    next_allowed = utc_now()
    service.forgotten_report.return_value = ForgottenThrottled(
        next_allowed_at=next_allowed
    )

    await quiz_handlers.cmd_forgotten(message, user_service, profile_service, service)

    rendered = message.answer.await_args.args[0]
    assert "раз в неделю" in rendered
    assert next_allowed.strftime("%d.%m.%Y") in rendered
