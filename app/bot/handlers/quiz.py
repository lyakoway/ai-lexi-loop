"""Active-recall quiz flow (Stage 6.4.6).

``/quiz`` serves one question (the Russian translation of a served
card); the learner's next text message in the quiz state is the
free-form answer, graded by the LLM on meaning. The verdict feeds the
same SM-2 path as reviews, the consecutive-success counter and — after
N correct answers in a row plus an explicit button press — a CEFR
promotion. One good answer never changes the level; a failed evaluation
shows a friendly retry text and leaves the schedule untouched.
``/forgotten`` renders the weekly "top forgotten" report.

All model/dictionary texts are escaped before rendering: the chat runs
with Telegram HTML parse mode.
"""

import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers.navigation import resolve_user_and_profile
from app.bot.keyboards.navigation import profile_empty_keyboard
from app.bot.keyboards.quiz import QuizCB, level_up_keyboard
from app.repositories import StorageError
from app.services import ProfileService, QuizService, UserService
from app.services.quiz import (
    ForgottenThrottled,
    QuizGradeResult,
    QuizUnavailable,
)

logger = logging.getLogger(__name__)

router = Router(name="quiz")


class QuizFlow(StatesGroup):
    """FSM state waiting for the free-form answer to one quiz question."""

    waiting_answer = State()


def _escape(value: str) -> str:
    return html.escape(value, quote=False)


def _friendly_text(reason: str) -> str:
    """Map a QuizService reason to a user-facing text (no internals).

    Renders strictly from ``texts.QUIZ_ERROR_TEXTS``: a reason string
    must never leak into the answer (enforced by a parametrized test).
    """
    return texts.QUIZ_ERROR_TEXTS.get(reason, texts.QUIZ_UNAVAILABLE)


def _render_grade(result: QuizGradeResult) -> str:
    """Render the grading verdict (all model text escaped)."""
    verdict = texts.QUIZ_CORRECT if result.is_correct else texts.QUIZ_WRONG
    parts = [
        verdict,
        "",
        texts.QUIZ_EXPLANATION_TITLE.format(explanation=_escape(result.explanation)),
    ]
    if not result.is_correct and result.corrected:
        parts.append("")
        parts.append(f"{texts.QUIZ_CORRECTED_TITLE} {_escape(result.corrected)}")
    if result.srs is not None:
        parts.append("")
        parts.append(
            texts.QUIZ_NEXT_REVIEW.format(
                date=result.srs.next_review_at.strftime("%d.%m.%Y")
            )
        )
    if result.level_offer:
        parts.append(texts.QUIZ_LEVEL_OFFER.format(streak=result.streak))
    return "\n".join(parts)


@router.message(Command("quiz"))
async def cmd_quiz(
    message: Message,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
    quiz_service: QuizService,
) -> None:
    """Serve one active-recall question."""
    if message.from_user is None:
        return
    try:
        user, profile = await resolve_user_and_profile(
            message.from_user.id, user_service, profile_service
        )
    except StorageError:
        logger.exception(
            "failed to load user for quiz, telegram_user_id=%s", message.from_user.id
        )
        await message.answer(texts.QUIZ_UNAVAILABLE)
        return
    if profile is None:
        await message.answer(texts.PROFILE_EMPTY, reply_markup=profile_empty_keyboard())
        return

    question = await quiz_service.start_quiz(user.id)
    if isinstance(question, QuizUnavailable):
        await message.answer(_friendly_text(question.reason))
        return
    await state.set_state(QuizFlow.waiting_answer)
    await state.update_data(
        user_id=user.id,
        expression_id=question.expression_id,
    )
    await message.answer(
        texts.QUIZ_QUESTION.format(question=_escape(question.question_translation))
    )


@router.message(StateFilter(QuizFlow.waiting_answer), F.text)
async def on_quiz_answer(
    message: Message,
    state: FSMContext,
    quiz_service: QuizService,
) -> None:
    """Grade the learner's answer and render the verdict."""
    if message.from_user is None or message.text is None:
        return
    data: dict[str, Any] = await state.get_data()
    await state.clear()
    if message.text.startswith("/"):
        # Any bot command exits the quiz instead of being graded as an
        # answer (aiogram would not match it to command handlers later:
        # this router runs first for the quiz state).
        await message.answer(texts.QUIZ_EXITED)
        return
    user_id = data.get("user_id")
    expression_id = data.get("expression_id")
    if user_id is None or expression_id is None:
        await message.answer(texts.QUIZ_STALE)
        return

    result = await quiz_service.grade_answer(
        int(user_id), int(expression_id), message.text
    )
    if isinstance(result, QuizUnavailable):
        await message.answer(_friendly_text(result.reason))
        return

    if result.level_offer:
        await message.answer(
            _render_grade(result),
            reply_markup=level_up_keyboard(),
        )
        return
    await message.answer(_render_grade(result))


@router.message(StateFilter(QuizFlow.waiting_answer))
async def on_quiz_non_text(message: Message) -> None:
    """Non-text content in the quiz state: hint, no LLM call."""
    await message.answer(texts.QUIZ_NON_TEXT)


@router.callback_query(QuizCB.filter(F.action == "level_up"))
async def on_level_up_confirm(
    callback: CallbackQuery,
    user_service: UserService,
    profile_service: ProfileService,
    quiz_service: QuizService,
) -> None:
    """Apply the confirmed promotion (the service re-checks the streak)."""
    await callback.answer()
    if callback.from_user is None:
        return
    try:
        user, _ = await resolve_user_and_profile(
            callback.from_user.id, user_service, profile_service
        )
    except StorageError:
        await callback.answer(texts.QUIZ_UNAVAILABLE, show_alert=True)
        return
    new_level = await quiz_service.confirm_level_up(user.id)
    if callback.message is None or not isinstance(callback.message, Message):
        return
    if new_level is None:
        await callback.message.answer(texts.QUIZ_LEVEL_UP_DECLINED)
        return
    await callback.message.answer(texts.QUIZ_LEVEL_UP_DONE.format(level=new_level))


@router.message(Command("forgotten"))
async def cmd_forgotten(
    message: Message,
    user_service: UserService,
    profile_service: ProfileService,
    quiz_service: QuizService,
) -> None:
    """Show the weekly "top forgotten" report (throttled)."""
    if message.from_user is None:
        return
    try:
        user, profile = await resolve_user_and_profile(
            message.from_user.id, user_service, profile_service
        )
    except StorageError:
        logger.exception(
            "failed to load user for forgotten report, telegram_user_id=%s",
            message.from_user.id,
        )
        await message.answer(texts.QUIZ_UNAVAILABLE)
        return
    if profile is None:
        await message.answer(texts.PROFILE_EMPTY, reply_markup=profile_empty_keyboard())
        return

    report = await quiz_service.forgotten_report(user.id)
    if isinstance(report, ForgottenThrottled):
        await message.answer(
            texts.FORGOTTEN_THROTTLED.format(
                date=report.next_allowed_at.strftime("%d.%m.%Y")
            )
        )
        return
    if not report.items:
        await message.answer(texts.FORGOTTEN_EMPTY)
        return
    lines = [texts.FORGOTTEN_TITLE]
    for number, item in enumerate(report.items, start=1):
        lines.append(
            texts.FORGOTTEN_ITEM.format(
                number=number,
                text=_escape(item.text),
                translation=_escape(item.translation),
                lapses=item.lapse_count,
            )
        )
    await message.answer("\n".join(lines))
