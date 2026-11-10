"""Tutor handlers: the «Объяснить» action on lesson and review cards.

The explain view borrows the message of the active flow: the callback
carries the card position it was rendered for, and the handler checks
it against whichever flow (lesson or review) is active. The FSM state
of the flow is never touched, so answering, navigation and the stale
protection keep working; the back button restores the exact card view.
"""

import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot import texts
from app.bot.handlers.lesson import LessonFlow
from app.bot.handlers.review import ReviewFlow
from app.bot.keyboards.lesson import lesson_card_keyboard
from app.bot.keyboards.review import review_card_keyboard
from app.bot.keyboards.tutor import TutorCB, back_to_card_keyboard
from app.bot.lesson_view import format_card
from app.bot.messages import safe_edit
from app.bot.tutor_view import format_explanation
from app.repositories import StorageError
from app.services import LessonService, TutorService
from app.services.tutor import TutorExplanation

logger = logging.getLogger(__name__)

router = Router(name="tutor")


async def _active_flow(
    state: FSMContext,
) -> tuple[str, dict[str, Any]] | None:
    """Return ``(flow_name, data)`` of the active lesson/review flow."""
    current: str | None = await state.get_state()
    if current == LessonFlow.active.state:
        flow_name = "lesson"
    elif current == ReviewFlow.active.state:
        flow_name = "review"
    else:
        return None
    data = await state.get_data()
    if not data.get("expression_ids") or "token" not in data:
        return None
    return flow_name, data


def _card_matches(callback_data: TutorCB, data: dict[str, Any]) -> bool:
    """Whether the pressed button belongs to the card being shown."""
    return callback_data.token == data.get("token") and callback_data.index == data.get(
        "index"
    )


@router.callback_query(TutorCB.filter(F.action == "explain"))
async def on_explain(
    callback: CallbackQuery,
    callback_data: TutorCB,
    state: FSMContext,
    tutor_service: TutorService,
    lesson_service: LessonService,
) -> None:
    """Fetch an AI explanation for the current card and show it."""
    flow = await _active_flow(state)
    if flow is None or not _card_matches(callback_data, flow[1]):
        await callback.answer(texts.TUTOR_STALE, show_alert=False)
        return
    flow_name, data = flow
    await callback.answer(texts.TUTOR_LOADING)

    expression_ids: list[int] = list(data["expression_ids"])
    index = int(data["index"])
    expression_id = expression_ids[index]
    # Repeat presses of the same card reuse the cached view instead of
    # paying for (and double-counting) another LLM call. The cache is
    # ephemeral navigation data: it lives in the flow's FSM state and
    # dies with it; a different card never matches the cache key.
    cache_key = f"{callback_data.token}:{callback_data.index}"
    cached = data.get("explanation_view")
    if isinstance(cached, dict) and cached.get("key") == cache_key:
        await safe_edit(
            callback.message,
            str(cached.get("text", "")),
            back_to_card_keyboard(callback_data.token, callback_data.index),
        )
        return
    try:
        result = await tutor_service.explain_expression(
            int(data["user_id"]), expression_id
        )
    except StorageError:
        logger.exception(
            "tutor explanation storage failure, user_id=%s", data["user_id"]
        )
        await safe_edit(
            callback.message,
            texts.TUTOR_UNAVAILABLE,
            back_to_card_keyboard(callback_data.token, callback_data.index),
        )
        return

    if isinstance(result, TutorExplanation):
        view_text = format_explanation(result.expression.text, result.explanation)
        await state.update_data(explanation_view={"key": cache_key, "text": view_text})
        await safe_edit(
            callback.message,
            view_text,
            back_to_card_keyboard(callback_data.token, callback_data.index),
        )
        return
    logger.info(
        "tutor explanation unavailable (%s) in %s flow", result.reason, flow_name
    )
    await safe_edit(
        callback.message,
        texts.TUTOR_UNAVAILABLE,
        back_to_card_keyboard(callback_data.token, callback_data.index),
    )


@router.callback_query(TutorCB.filter(F.action == "back"))
async def on_back_to_card(
    callback: CallbackQuery,
    callback_data: TutorCB,
    state: FSMContext,
    lesson_service: LessonService,
    data: dict[str, Any] | None = None,
) -> None:
    """Restore the card view of the active flow."""
    flow = await _active_flow(state)
    if flow is None or not _card_matches(callback_data, flow[1]):
        await callback.answer(texts.TUTOR_STALE, show_alert=False)
        return
    flow_name, data = flow
    await callback.answer()

    expression_ids: list[int] = list(data["expression_ids"])
    index = int(data["index"])
    total = len(expression_ids)
    try:
        expressions = await lesson_service.get_expressions(expression_ids)
    except StorageError:
        logger.exception("failed to restore the lesson card view")
        await callback.answer(texts.TUTOR_UNAVAILABLE, show_alert=True)
        return
    expressions_by_id = {expression.id: expression for expression in expressions}
    expression = expressions_by_id.get(expression_ids[index])
    if expression is None:
        logger.error("card %s disappeared while explaining", expression_ids[index])
        await callback.answer(texts.TUTOR_STALE, show_alert=True)
        return
    if flow_name == "review":
        await safe_edit(
            callback.message,
            f"{texts.REVIEW_PREFIX}\n\n"
            + format_card(expression, index=index, total=total),
            review_card_keyboard(
                token=callback_data.token,
                index=index,
                expression_id=expression.id,
                with_audio=bool((data or {}).get("tts_available")),
            ),
        )
        return
    await safe_edit(
        callback.message,
        format_card(expression, index=index, total=total),
        lesson_card_keyboard(
            token=callback_data.token,
            index=index,
            total=total,
            expression_id=expression.id,
            with_audio=bool((data or {}).get("tts_available")),
        ),
    )


@router.callback_query(TutorCB.filter())
async def on_stale_tutor_callback(callback: CallbackQuery) -> None:
    """Answer tutor buttons when no matching flow is active."""
    await callback.answer(texts.TUTOR_STALE, show_alert=False)
