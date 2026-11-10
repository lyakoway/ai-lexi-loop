"""Review flow: serve due cards and apply spaced-repetition answers.

Durable state lives in PostgreSQL (``expression_progress`` holds the SRS
scheduling); the FSM keeps only navigation data — the ordered expression
ids, the current index, the session token and the running answer counts.
Every answer callback carries the token and index it was rendered for;
mismatches with the current state mean a stale or repeated press and are
answered, never applied. If saving an answer fails, the card stays in
place so the user can press again — no false "success" is shown.
"""

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers.navigation import resolve_user_and_profile
from app.bot.keyboards.callbacks import MenuCB
from app.bot.keyboards.navigation import profile_empty_keyboard
from app.bot.keyboards.review import ReviewCB, review_card_keyboard
from app.bot.lesson_view import format_card
from app.bot.messages import safe_edit
from app.db.models import Profile
from app.repositories import StorageError
from app.services import ProfileService, ReviewService, UserService
from app.services.reviews import ReviewUnavailable

logger = logging.getLogger(__name__)

router = Router(name="review")


class ReviewFlow(StatesGroup):
    """Navigation state of one in-progress review session."""

    active = State()


async def start_review_for_message(
    message: Message,
    user_id: int,
    state: FSMContext,
    review_service: ReviewService,
    data: dict[str, Any] | None = None,
) -> None:
    """Build a review session and send its first card."""
    try:
        plan = await review_service.build_review(user_id)
    except StorageError:
        logger.exception("failed to build review for user_id=%s", user_id)
        await message.answer(texts.REVIEW_BUILD_FAILED)
        return

    if isinstance(plan, ReviewUnavailable):
        await message.answer(texts.REVIEW_NO_DUE)
        return

    expression_ids = [expression.id for expression in plan.expressions]
    await state.set_state(ReviewFlow.active)
    await state.update_data(
        user_id=user_id,
        expression_ids=expression_ids,
        index=0,
        token=expression_ids[0],
        remembered_count=0,
        forgot_count=0,
    )
    card = f"{texts.REVIEW_PREFIX}\n\n" + format_card(
        plan.expressions[0], index=0, total=len(expression_ids)
    )
    await message.answer(
        card,
        reply_markup=review_card_keyboard(
            token=expression_ids[0],
            index=0,
            expression_id=expression_ids[0],
            with_audio=bool((data or {}).get("tts_available")),
        ),
    )


async def _start_by_user(
    user_id: int,
    profile: Profile | None,
    message: Message,
    state: FSMContext,
    review_service: ReviewService,
    data: dict[str, Any] | None = None,
) -> None:
    if profile is None:
        await message.answer(texts.PROFILE_EMPTY, reply_markup=profile_empty_keyboard())
        return
    await start_review_for_message(message, user_id, state, review_service)


@router.message(Command("review"))
async def cmd_review(
    message: Message,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
    review_service: ReviewService,
    data: dict[str, Any] | None = None,
) -> None:
    """Start a review session, or invite to onboarding without a profile."""
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
        await message.answer(texts.REVIEW_BUILD_FAILED)
        return
    await _start_by_user(user.id, profile, message, state, review_service, data)


@router.callback_query(MenuCB.filter(F.action == "review"))
async def on_menu_review(
    callback: CallbackQuery,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
    review_service: ReviewService,
    data: dict[str, Any] | None = None,
) -> None:
    """Start a review session from the main-menu button."""
    await callback.answer()
    message = callback.message
    if not isinstance(message, Message):
        return
    try:
        user, profile = await resolve_user_and_profile(
            callback.from_user.id, user_service, profile_service
        )
    except StorageError:
        await message.answer(texts.REVIEW_BUILD_FAILED)
        return
    await _start_by_user(user.id, profile, message, state, review_service, data)


def _is_current_card(callback_data: ReviewCB, data: dict[str, Any]) -> bool:
    """Whether the pressed button belongs to the card being shown."""
    return callback_data.token == data.get("token") and callback_data.index == data.get(
        "index"
    )


@router.callback_query(ReviewCB.filter(F.action.in_({"remember", "forgot"})))
async def on_answer(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    state: FSMContext,
    review_service: ReviewService,
) -> None:
    """Apply one answer and show the next card or the summary."""
    if await state.get_state() != ReviewFlow.active.state:
        await callback.answer(texts.REVIEW_STALE, show_alert=False)
        return
    data = await state.get_data()
    if not _is_current_card(callback_data, data):
        await callback.answer(texts.REVIEW_STALE, show_alert=False)
        return

    remembered = callback_data.action == "remember"
    expression_ids: list[int] = list(data["expression_ids"])
    index = int(data["index"])
    expression_id = expression_ids[index]
    user_id = int(data["user_id"])
    try:
        update = await review_service.apply_answer(
            user_id, expression_id, remembered=remembered
        )
    except StorageError:
        logger.exception(
            "failed to apply review answer, user_id=%s expression_id=%s",
            user_id,
            expression_id,
        )
        await callback.answer(texts.REVIEW_ANSWER_FAILED, show_alert=True)
        return
    if update is None:
        # The progress row disappeared (stale session); drop the session.
        await state.clear()
        await callback.answer(texts.REVIEW_STALE, show_alert=True)
        return

    remembered_count = int(data.get("remembered_count", 0)) + int(remembered)
    forgot_count = int(data.get("forgot_count", 0)) + int(not remembered)
    await callback.answer("Помню" if remembered else "Забыл")

    next_index = index + 1
    if next_index >= len(expression_ids):
        await state.clear()
        try:
            await review_service.complete_session(
                user_id,
                remembered=remembered_count,
                forgot=forgot_count,
                source="telegram",
            )
        except StorageError:
            # Answers are already persisted in their own transactions;
            # only the summary event is lost here, which the log records.
            logger.exception("failed to record review summary, user_id=%s", user_id)
        await safe_edit(
            callback.message,
            texts.REVIEW_FINISHED.format(
                remembered=remembered_count, forgot=forgot_count
            ),
            None,
        )
        return

    try:
        expressions = await review_service.get_expressions(expression_ids)
    except StorageError:
        await callback.answer(texts.REVIEW_BUILD_FAILED, show_alert=True)
        return
    expressions_by_id = {expression.id: expression for expression in expressions}
    expression = expressions_by_id.get(expression_ids[next_index])
    if expression is None:
        logger.error(
            "review expression %s disappeared mid-session",
            expression_ids[next_index],
        )
        await state.clear()
        await callback.answer(texts.REVIEW_STALE, show_alert=True)
        return
    await state.update_data(
        index=next_index,
        remembered_count=remembered_count,
        forgot_count=forgot_count,
    )
    await safe_edit(
        callback.message,
        f"{texts.REVIEW_PREFIX}\n\n"
        + format_card(expression, index=next_index, total=len(expression_ids)),
        review_card_keyboard(
            token=data["token"],
            index=next_index,
            expression_id=expression_ids[next_index],
            with_audio=bool(data.get("tts_available")),
        ),
    )


@router.callback_query(ReviewCB.filter(), StateFilter(None))
async def on_stale_review_callback(callback: CallbackQuery) -> None:
    """Answer presses of review buttons when no session is active."""
    await callback.answer(texts.REVIEW_STALE, show_alert=False)
