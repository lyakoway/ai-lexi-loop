"""Level-placement diagnostic flow (Stage 6.4.7).

Entered from the onboarding level step via the "unknown level" option:
the LLM generates a short multiple-choice test for the target language,
the learner answers question by question (FSM holds the questions, the
current index and the answers), a deterministic rule recommends a CEFR
level and the learner confirms it or falls back to the manual choice.
Confirming writes the level into the onboarding draft and continues the
flow — nothing is saved to the database until the usual confirmation
screen.

LLM failures degrade to a friendly message and the manual choice; the
onboarding draft is never lost.
"""

import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery

from app.bot import texts
from app.bot.handlers.onboarding import (
    _STEP_TO_STATE,
    _next_step,
    show_step,
)
from app.bot.keyboards.placement import (
    PlacementCB,
    placement_question_keyboard,
    placement_result_keyboard,
)
from app.bot.messages import safe_edit
from app.domain.placement import recommend_level
from app.services import PlacementService
from app.services.placement import PlacementSet, PlacementUnavailable

logger = logging.getLogger(__name__)

router = Router(name="placement")

# FSM data keys of the running diagnostic (prefixed, so they never
# collide with the onboarding draft fields stored in the same state).
_QUESTIONS_KEY = "placement_questions"
_INDEX_KEY = "placement_index"
_ANSWERS_KEY = "placement_answers"


class PlacementFlow(StatesGroup):
    """States of a running diagnostic inside onboarding."""

    question = State()
    done = State()


def _escape(value: str) -> str:
    return html.escape(value, quote=False)


def _serialize(placement: PlacementSet) -> list[dict[str, Any]]:
    return [
        {
            "text": question.text,
            "options": list(question.options),
            "level": question.level,
            "correct_index": question.correct_index,
        }
        for question in placement.questions
    ]


def _render_question(questions: list[dict[str, Any]], index: int) -> tuple[str, Any]:
    question = questions[index]
    rendered = texts.PLACEMENT_QUESTION.format(
        number=index + 1,
        total=len(questions),
        question=_escape(str(question["text"])),
    )
    options = tuple(str(option) for option in question["options"])
    return rendered, placement_question_keyboard(index=index, options=options)


async def start_placement(
    callback: CallbackQuery,
    state: FSMContext,
    placement_service: PlacementService,
) -> None:
    """Generate the diagnostic and show its first question.

    The onboarding draft stays in the FSM data untouched; on any
    unavailability the level step is re-rendered for a manual choice.
    """
    data = await state.get_data()
    target = str(data.get("target_language") or "en")
    native = str(data.get("native_language") or "ru")
    placement = await placement_service.generate(
        target_language=target, native_language=native
    )
    if isinstance(placement, PlacementUnavailable):
        logger.info("placement unavailable for %s: %s", target, placement.reason)
        await callback.answer(texts.PLACEMENT_UNAVAILABLE, show_alert=True)
        await state.set_state(_STEP_TO_STATE["level"])
        if callback.message is not None:
            await show_step(callback.message, "level", state)
        return

    questions_data: list[dict[str, Any]] = _serialize(placement)
    patch: dict[str, Any] = {
        _QUESTIONS_KEY: questions_data,
        _INDEX_KEY: 0,
        _ANSWERS_KEY: [],
    }
    await state.update_data(patch)
    await state.set_state(PlacementFlow.question)
    rendered, keyboard = _render_question(questions_data, 0)
    await safe_edit(callback.message, rendered, keyboard)


@router.callback_query(
    PlacementCB.filter(F.action == "ans"), StateFilter(PlacementFlow.question)
)
async def on_placement_answer(
    callback: CallbackQuery,
    callback_data: PlacementCB,
    state: FSMContext,
) -> None:
    """Record the answer and show the next question or the result."""
    data = await state.get_data()
    questions: list[dict[str, Any]] = list(data.get(_QUESTIONS_KEY) or [])
    index = int(data.get(_INDEX_KEY) or 0)
    answers: list[int] = list(data.get(_ANSWERS_KEY) or [])
    if (
        not questions
        or callback_data.index != index
        or callback_data.value >= len(questions[index]["options"])
    ):
        await callback.answer(texts.PLACEMENT_STALE, show_alert=False)
        return

    answers.append(int(callback_data.value))
    answers_patch: dict[str, Any] = {_ANSWERS_KEY: answers}
    await state.update_data(answers_patch)
    await callback.answer()

    next_index = index + 1
    if next_index < len(questions):
        index_patch: dict[str, Any] = {_INDEX_KEY: next_index}
        await state.update_data(index_patch)
        rendered, keyboard = _render_question(questions, next_index)
        await safe_edit(callback.message, rendered, keyboard)
        return

    verdicts = [
        (str(question["level"]), answer == int(question["correct_index"]))
        for question, answer in zip(questions, answers, strict=True)
    ]
    level = recommend_level(verdicts)
    await state.set_state(PlacementFlow.done)
    await safe_edit(
        callback.message,
        texts.PLACEMENT_RECOMMENDED.format(level=level),
        placement_result_keyboard(level),
    )


@router.callback_query(
    PlacementCB.filter(F.action == "confirm"), StateFilter(PlacementFlow.done)
)
async def on_placement_confirm(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Write the recommended level into the draft and continue."""
    data = await state.get_data()
    questions: list[dict[str, Any]] = list(data.get(_QUESTIONS_KEY) or [])
    answers: list[int] = list(data.get(_ANSWERS_KEY) or [])
    if not questions or len(answers) != len(questions):
        await callback.answer(texts.PLACEMENT_STALE, show_alert=False)
        return
    verdicts = [
        (str(question["level"]), answer == int(question["correct_index"]))
        for question, answer in zip(questions, answers, strict=True)
    ]
    level = recommend_level(verdicts)
    confirm_patch: dict[str, Any] = {
        "level": level,
        _QUESTIONS_KEY: None,
        _INDEX_KEY: 0,
        _ANSWERS_KEY: [],
    }
    await state.update_data(confirm_patch)
    await callback.answer()
    following = _next_step("level")
    assert following is not None  # "level" is never the last step
    await state.set_state(_STEP_TO_STATE[following])
    if callback.message is not None:
        await show_step(callback.message, following, state)


@router.callback_query(
    PlacementCB.filter(F.action == "manual"), StateFilter(PlacementFlow.done)
)
async def on_placement_manual(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Drop the diagnostic and return to the manual level choice."""
    reset_patch: dict[str, Any] = {
        _QUESTIONS_KEY: None,
        _INDEX_KEY: 0,
        _ANSWERS_KEY: [],
    }
    await state.update_data(reset_patch)
    await callback.answer()
    await state.set_state(_STEP_TO_STATE["level"])
    if callback.message is not None:
        await show_step(callback.message, "level", state)


@router.callback_query(PlacementCB.filter(F.action == "ans"))
async def on_stale_answer(callback: CallbackQuery) -> None:
    """Answer presses with no active diagnostic or a finished one.

    Registered after the state-scoped handler, so it only catches
    presses that no live question is waiting for.
    """
    await callback.answer(texts.PLACEMENT_STALE, show_alert=False)
