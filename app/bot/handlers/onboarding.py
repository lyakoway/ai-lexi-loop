"""Onboarding flow: a step-by-step profile setup driven by FSM.

The draft lives in aiogram's FSM storage (in-memory by default): each
answer is written into the state data, and nothing touches the database
until the user presses «Всё верно» on the confirmation screen. Every
callback carries the step it was rendered for, so presses of buttons
from finished steps are rejected instead of being applied to the
current one.

Restart note: FSM state is kept in memory, so a mid-flow application
restart drops the unfinished draft; the saved profile in PostgreSQL is
never touched until an explicit confirmation.
"""

import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    Message,
)
from pydantic import ValidationError

from app.bot import texts
from app.bot.keyboards.callbacks import OnboardingCB
from app.bot.keyboards.navigation import main_menu_keyboard
from app.bot.keyboards.onboarding import (
    SKIP_DEFAULTS,
    STEP_ORDER,
    confirm_keyboard,
    onboarding_step_keyboard,
    step_values,
)
from app.bot.messages import safe_edit
from app.bot.profile_view import format_draft
from app.domain.profile import resolve_timezone
from app.repositories import StorageError
from app.schemas.profile import ProfileCreate
from app.services import ProfileService, UserService

logger = logging.getLogger(__name__)

router = Router(name="onboarding")


class OnboardingFlow(StatesGroup):
    """One state per onboarding step plus input and confirmation states."""

    native_language = State()
    target_language = State()
    level = State()
    learning_goal = State()
    topics = State()
    preferred_accent = State()
    daily_study_minutes = State()
    new_items_per_day = State()
    lesson_time = State()
    review_time = State()
    timezone = State()
    timezone_input = State()
    confirm = State()


_STEP_TO_STATE = {step: getattr(OnboardingFlow, step) for step in STEP_ORDER}
_STATE_TO_STEP = {state.state: step for step, state in _STEP_TO_STATE.items()}
_CONFIRM_STATE = OnboardingFlow.confirm.state
_TIMEZONE_INPUT_STATE = OnboardingFlow.timezone_input.state


def _next_step(step: str) -> str | None:
    index = STEP_ORDER.index(step) + 1
    return STEP_ORDER[index] if index < len(STEP_ORDER) else None


def _previous_step(step: str) -> str | None:
    index = STEP_ORDER.index(step) - 1
    return STEP_ORDER[index] if index >= 0 else None


async def _current_step(state: FSMContext) -> str | None:
    """Step key for the current state, or ``None`` outside the flow."""
    current = await state.get_state()
    if current is None:
        return None
    return _STATE_TO_STEP.get(current)


async def show_step(
    message: Message | InaccessibleMessage | None,
    step: str,
    state: FSMContext,
) -> None:
    """Render the current step message from FSM data."""
    draft = await state.get_data()
    await safe_edit(
        message, texts.ONBOARDING_PROMPTS[step], onboarding_step_keyboard(step, draft)
    )


async def show_confirm(
    message: Message | InaccessibleMessage | None, state: FSMContext
) -> None:
    """Render the confirmation screen, validating the draft first."""
    draft = await state.get_data()
    try:
        ProfileCreate.model_validate(draft)
    except ValidationError:
        logger.warning("onboarding draft failed validation: %s", draft)
        await state.clear()
        await safe_edit(
            message,
            "Черновик профиля оказался некорректным. Начните настройку "
            "заново командой /settings.",
            main_menu_keyboard(),
        )
        return
    await state.set_state(OnboardingFlow.confirm)
    await safe_edit(
        message,
        format_draft(draft) + texts.ONBOARDING_CONFIRM_PROMPT,
        confirm_keyboard(),
    )


async def start_onboarding(
    callback: CallbackQuery,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Entry point: reset the draft (prefilled when a profile exists)."""
    await callback.answer()
    if callback.message is None:
        return
    telegram_user_id = callback.from_user.id
    try:
        user, _ = await user_service.ensure_registered(telegram_user_id)
        profile = await profile_service.get_profile(user.id)
        draft = profile_service.build_draft(profile) if profile else {}
    except StorageError:
        logger.exception(
            "failed to load user before onboarding, telegram_user_id=%s",
            telegram_user_id,
        )
        await callback.message.answer(
            "Не удалось открыть настройку — сервис временно недоступен. "
            "Попробуйте ещё раз чуть позже."
        )
        return
    await state.set_data(draft)
    await state.set_state(OnboardingFlow.native_language)
    await show_step(callback.message, "native_language", state)


@router.callback_query(OnboardingCB.filter(F.action == "ans"))
async def on_answer(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
) -> None:
    """Store a single-choice answer and move to the next step."""
    step = await _current_step(state)
    if step is None or step != callback_data.step or step == "topics":
        await callback.answer(texts.ONBOARDING_STALE, show_alert=False)
        return
    if callback_data.value not in step_values(step):
        await callback.answer(texts.ONBOARDING_INVALID_ANSWER)
        return
    await state.update_data({step: callback_data.value})
    await callback.answer()
    following = _next_step(step)
    if following is None:
        await show_confirm(callback.message, state)
    else:
        await state.set_state(_STEP_TO_STATE[following])
        await show_step(callback.message, following, state)


@router.callback_query(OnboardingCB.filter(F.action == "toggle"))
async def on_topic_toggle(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
) -> None:
    """Toggle one topic in the draft and re-render the topics step."""
    step = await _current_step(state)
    if step != "topics" or callback_data.step != "topics":
        await callback.answer(texts.ONBOARDING_STALE, show_alert=False)
        return
    all_values = step_values("topics")
    if callback_data.value not in all_values:
        await callback.answer(texts.ONBOARDING_INVALID_ANSWER)
        return
    draft = await state.get_data()
    selected = list(draft.get("topics") or [])
    if callback_data.value in selected:
        selected.remove(callback_data.value)
    else:
        selected.append(callback_data.value)
    await state.update_data(topics=selected)
    await callback.answer()
    await show_step(callback.message, "topics", state)


@router.callback_query(OnboardingCB.filter(F.action == "next"))
async def on_topics_done(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
) -> None:
    """Finish the topics step with the current selection (may be empty)."""
    step = await _current_step(state)
    if step != "topics" or callback_data.step != "topics":
        await callback.answer(texts.ONBOARDING_STALE, show_alert=False)
        return
    await callback.answer()
    await state.set_state(OnboardingFlow.preferred_accent)
    await show_step(callback.message, "preferred_accent", state)


@router.callback_query(OnboardingCB.filter(F.action == "back"))
async def on_back(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
) -> None:
    """Return to the previous step keeping all draft answers."""
    step = await _current_step(state)
    if step is None or step != callback_data.step:
        await callback.answer(texts.ONBOARDING_STALE, show_alert=False)
        return
    previous = _previous_step(step)
    if previous is None:
        await callback.answer(texts.ONBOARDING_BACK_UNAVAILABLE)
        return
    await callback.answer()
    await state.set_state(_STEP_TO_STATE[previous])
    await show_step(callback.message, previous, state)


@router.callback_query(OnboardingCB.filter(F.action == "skip"))
async def on_skip(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
) -> None:
    """Skip an optional step, storing its default value."""
    step = await _current_step(state)
    if step is None or step != callback_data.step:
        await callback.answer(texts.ONBOARDING_STALE, show_alert=False)
        return
    if step not in SKIP_DEFAULTS:
        await callback.answer(texts.ONBOARDING_CANT_SKIP)
        return
    await state.update_data({step: SKIP_DEFAULTS[step]})
    await callback.answer()
    following = _next_step(step)
    if following is None:
        await show_confirm(callback.message, state)
    else:
        await state.set_state(_STEP_TO_STATE[following])
        await show_step(callback.message, following, state)


@router.callback_query(OnboardingCB.filter(F.action == "cancel"))
async def on_cancel(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
) -> None:
    """Abort the flow: saved settings (if any) stay untouched."""
    if await state.get_state() is None:
        await callback.answer(texts.ONBOARDING_STALE, show_alert=False)
        return
    await state.clear()
    await callback.answer("Настройка отменена")
    await safe_edit(
        callback.message,
        texts.ONBOARDING_CANCELLED,
        main_menu_keyboard(),
    )


@router.callback_query(OnboardingCB.filter(F.action == "restart"))
async def on_restart(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
) -> None:
    """Start the whole flow over from an empty draft."""
    await state.set_data({})
    await state.set_state(OnboardingFlow.native_language)
    await callback.answer()
    await show_step(callback.message, "native_language", state)


@router.callback_query(OnboardingCB.filter(F.action == "tzother"))
async def on_timezone_other(
    callback: CallbackQuery,
    callback_data: OnboardingCB,
    state: FSMContext,
) -> None:
    """Switch the timezone step to free-form text input."""
    step = await _current_step(state)
    if step != "timezone" or callback_data.step != "timezone":
        await callback.answer(texts.ONBOARDING_STALE, show_alert=False)
        return
    await state.set_state(OnboardingFlow.timezone_input)
    await callback.answer()
    await safe_edit(callback.message, texts.TIMEZONE_INPUT_PROMPT, None)


@router.message(StateFilter(OnboardingFlow.timezone_input), F.text)
async def on_timezone_text(message: Message, state: FSMContext) -> None:
    """Resolve a typed city or zone name, then continue the flow."""
    resolved = resolve_timezone(message.text or "")
    if resolved is None:
        await message.answer(texts.TIMEZONE_NOT_FOUND.format(query=message.text))
        return
    await state.update_data(timezone=resolved)
    await message.answer(f"Часовой пояс: <b>{resolved}</b> ✅")
    # The flow message is buried under the user's text messages by now,
    # so the confirmation is sent as a fresh message instead of an edit.
    draft = await state.get_data()
    try:
        ProfileCreate.model_validate(draft)
    except ValidationError:
        await state.clear()
        await message.answer(
            "Черновик профиля оказался некорректным. Начните настройку "
            "заново командой /settings."
        )
        return
    await state.set_state(OnboardingFlow.confirm)
    await message.answer(
        format_draft(draft) + texts.ONBOARDING_CONFIRM_PROMPT,
        reply_markup=confirm_keyboard(),
    )


@router.callback_query(
    OnboardingCB.filter(F.action == "confirm"),
    StateFilter(OnboardingFlow.confirm),
)
async def on_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Persist the confirmed draft as the user's profile."""
    draft = await state.get_data()
    try:
        data = ProfileCreate.model_validate(draft)
    except ValidationError:
        await state.clear()
        await callback.answer("Черновик устарел, начните заново", show_alert=True)
        await safe_edit(
            callback.message,
            "Черновик профиля оказался некорректным. Начните настройку "
            "заново командой /settings.",
            main_menu_keyboard(),
        )
        return

    telegram_user_id = callback.from_user.id
    try:
        user, _ = await user_service.ensure_registered(telegram_user_id)
        await profile_service.save_profile(user.id, data)
    except StorageError:
        logger.exception(
            "failed to save profile, telegram_user_id=%s", telegram_user_id
        )
        await callback.answer(
            "Не удалось сохранить профиль, попробуйте ещё раз", show_alert=True
        )
        return

    await state.clear()
    await callback.answer("Сохранено")
    await safe_edit(callback.message, texts.ONBOARDING_SAVED, main_menu_keyboard())


@router.callback_query(OnboardingCB.filter(), StateFilter(None))
async def on_stale_callback(callback: CallbackQuery) -> None:
    """Answer presses of onboarding buttons when no flow is active."""
    await callback.answer(texts.ONBOARDING_STALE, show_alert=False)
