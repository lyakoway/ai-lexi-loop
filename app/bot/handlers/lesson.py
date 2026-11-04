"""Lesson flow: build a lesson, navigate cards, finish with progress.

Persistence lives in PostgreSQL (progress rows are created when the
lesson is built), while the FSM holds only the ephemeral navigation
state: the ordered expression ids, the current index and the lesson
token (the first expression id). Every callback carries the token and
index it was rendered for; mismatches with the current state mean a
stale button press and are answered, not applied.

After an application restart the FSM state is gone, so old lesson
buttons answer «урок неактивен»; the progress recorded in the database
is unaffected.
"""

import logging
import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers.navigation import resolve_user_and_profile
from app.bot.keyboards.callbacks import MenuCB
from app.bot.keyboards.lesson import LessonCB, lesson_card_keyboard
from app.bot.keyboards.navigation import profile_empty_keyboard
from app.bot.lesson_view import format_card
from app.bot.messages import safe_edit
from app.db.models import Profile
from app.repositories import StorageError
from app.services import LessonService, ProfileService, UserService
from app.services.lessons import LessonUnavailable

logger = logging.getLogger(__name__)

router = Router(name="lesson")


class LessonFlow(StatesGroup):
    """Navigation state of one in-progress lesson."""

    active = State()


async def start_lesson_for_message(
    message: Message,
    user_id: int,
    profile: Profile,
    state: FSMContext,
    lesson_service: LessonService,
) -> None:
    """Build a lesson and send its first card."""
    try:
        plan = await lesson_service.build_lesson(user_id, profile)
    except StorageError:
        logger.exception("failed to build lesson for user_id=%s", user_id)
        await message.answer(texts.LESSON_BUILD_FAILED)
        return

    if isinstance(plan, LessonUnavailable):
        await message.answer(
            texts.LESSON_UNAVAILABLE_MESSAGES[plan.reason].format(level=profile.level)
        )
        return

    expressions = plan.expressions
    expression_ids = [expression.id for expression in expressions]
    await state.set_state(LessonFlow.active)
    await state.update_data(
        user_id=user_id,
        expression_ids=expression_ids,
        index=0,
        token=expression_ids[0],
        lesson_ref=plan.lesson_ref,
        started_at=time.time(),
    )
    card = format_card(expressions[0], index=0, total=len(expressions))
    if plan.used_default_level:
        card = f"{texts.LESSON_DEFAULT_LEVEL_NOTE}\n\n{card}"
    await message.answer(
        card,
        reply_markup=lesson_card_keyboard(
            token=expression_ids[0], index=0, total=len(expressions)
        ),
    )


@router.message(Command("lesson"))
async def cmd_lesson(
    message: Message,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
    lesson_service: LessonService,
) -> None:
    """Start a lesson, or invite to onboarding when there is no profile."""
    if message.from_user is None:
        return
    try:
        user, profile = await resolve_user_and_profile(
            message.from_user.id, user_service, profile_service
        )
    except StorageError:
        logger.exception(
            "failed to load user, telegram_user_id=%s", message.from_user.id
        )
        await message.answer(texts.LESSON_BUILD_FAILED)
        return
    if profile is None:
        await message.answer(texts.PROFILE_EMPTY, reply_markup=profile_empty_keyboard())
        return
    await start_lesson_for_message(message, user.id, profile, state, lesson_service)


@router.callback_query(MenuCB.filter(F.action == "lesson"))
async def on_menu_lesson(
    callback: CallbackQuery,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
    lesson_service: LessonService,
) -> None:
    """Start a lesson from the main-menu button."""
    await callback.answer()
    message = callback.message
    if not isinstance(message, Message):
        return
    try:
        user, profile = await resolve_user_and_profile(
            callback.from_user.id, user_service, profile_service
        )
    except StorageError:
        await message.answer(texts.LESSON_BUILD_FAILED)
        return
    if profile is None:
        await message.answer(texts.PROFILE_EMPTY, reply_markup=profile_empty_keyboard())
        return
    await start_lesson_for_message(message, user.id, profile, state, lesson_service)


async def _lesson_state_data(
    callback: CallbackQuery, callback_data: LessonCB, state: FSMContext
) -> dict[str, Any] | None:
    """Validate the pressed button against the active lesson, or None."""
    if await state.get_state() != LessonFlow.active.state:
        return None
    data = await state.get_data()
    if callback_data.token != data.get("token"):
        return None
    if callback_data.index != data.get("index"):
        return None
    return data


@router.callback_query(LessonCB.filter(F.action == "next"))
async def on_next_card(
    callback: CallbackQuery,
    callback_data: LessonCB,
    state: FSMContext,
    lesson_service: LessonService,
) -> None:
    """Show the next card of the active lesson."""
    data = await _lesson_state_data(callback, callback_data, state)
    if data is None:
        await callback.answer(texts.LESSON_STALE, show_alert=False)
        return
    expression_ids: list[int] = list(data["expression_ids"])
    next_index = int(data["index"]) + 1
    if next_index >= len(expression_ids):
        # Defensive: the last card has no «next» button, so this is a
        # stale or hand-crafted callback.
        await callback.answer(texts.LESSON_STALE, show_alert=False)
        return
    try:
        expressions = await lesson_service.get_expressions(expression_ids)
    except StorageError:
        await callback.answer(texts.LESSON_BUILD_FAILED, show_alert=True)
        return
    expressions_by_id = {expression.id: expression for expression in expressions}
    expression = expressions_by_id.get(expression_ids[next_index])
    if expression is None:
        logger.error(
            "lesson expression %s disappeared mid-lesson", expression_ids[next_index]
        )
        await callback.answer(texts.LESSON_STALE, show_alert=True)
        return
    await state.update_data(index=next_index)
    await callback.answer()
    await safe_edit(
        callback.message,
        format_card(expression, index=next_index, total=len(expression_ids)),
        lesson_card_keyboard(
            token=data["token"], index=next_index, total=len(expression_ids)
        ),
    )


@router.callback_query(LessonCB.filter(F.action == "finish"))
async def on_finish_lesson(
    callback: CallbackQuery,
    callback_data: LessonCB,
    state: FSMContext,
    lesson_service: LessonService,
) -> None:
    """Complete the lesson and mark the shown expressions as done."""
    data = await _lesson_state_data(callback, callback_data, state)
    if data is None:
        await callback.answer(texts.LESSON_STALE, show_alert=False)
        return
    expression_ids: list[int] = list(data["expression_ids"])
    shown_count = int(data["index"]) + 1
    user_id = int(data["user_id"])
    started_at = float(data.get("started_at") or 0.0)
    duration_seconds = time.time() - started_at if started_at > 0 else None
    try:
        completed = await lesson_service.finish_lesson(
            user_id,
            expression_ids[:shown_count],
            source="telegram",
            lesson_ref=data.get("lesson_ref"),
            expressions_total=len(expression_ids),
            duration_seconds=duration_seconds,
        )
    except StorageError:
        logger.exception("failed to finish lesson")
        await callback.answer(texts.LESSON_SAVE_FAILED, show_alert=True)
        return
    await state.clear()
    await callback.answer("Урок завершён")
    await safe_edit(
        callback.message,
        texts.LESSON_FINISHED.format(count=completed),
        None,
    )


@router.callback_query(LessonCB.filter(), StateFilter(None))
async def on_stale_lesson_callback(callback: CallbackQuery) -> None:
    """Answer presses of lesson buttons when no lesson is active."""
    await callback.answer(texts.LESSON_STALE, show_alert=False)
