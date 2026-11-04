"""Settings editor: change one field at a time.

Single-choice fields are stateless: the callback carries the field and
the value, the service applies a one-field patch, and the menu re-renders
with fresh data. Topics (multi-select) and custom timezone entry use a
small FSM group to hold the in-progress choice.
"""

import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, Message
from pydantic import ValidationError

from app.bot import texts
from app.bot.handlers.onboarding import start_onboarding
from app.bot.keyboards.callbacks import SettingsCB
from app.bot.keyboards.navigation import back_to_menu_keyboard
from app.bot.keyboards.onboarding import step_values
from app.bot.keyboards.settings import (
    EDITABLE_FIELDS,
    field_options_keyboard,
    settings_menu_keyboard,
    topics_edit_keyboard,
)
from app.bot.messages import safe_edit
from app.bot.profile_view import settings_menu_text
from app.db.models import Profile, User
from app.domain.profile import resolve_timezone
from app.repositories import StorageError
from app.schemas.profile import ProfileUpdate
from app.services import ProfileNotConfiguredError, ProfileService, UserService

logger = logging.getLogger(__name__)

router = Router(name="settings")


class SettingsFlow(StatesGroup):
    """Multi-step states of the settings editor."""

    topics = State()
    timezone_input = State()


async def _load_profile(
    telegram_user_id: int,
    user_service: UserService,
    profile_service: ProfileService,
) -> tuple[User, Profile | None]:
    user, _ = await user_service.ensure_registered(telegram_user_id)
    profile = await profile_service.get_profile(user.id)
    return user, profile


async def _show_settings_menu(
    message: Message | InaccessibleMessage | None,
    user_id: int,
    profile_service: ProfileService,
) -> None:
    """Re-render the settings menu with current values."""
    profile = await profile_service.get_profile(user_id)
    if profile is None or message is None:
        return
    await safe_edit(message, settings_menu_text(profile), settings_menu_keyboard())


@router.callback_query(SettingsCB.filter(F.action == "onboarding"))
async def on_open_onboarding(
    callback: CallbackQuery,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Entry into the full onboarding flow (re-run or first run)."""
    await start_onboarding(callback, state, user_service, profile_service)


@router.callback_query(SettingsCB.filter(F.action == "menu"))
async def on_menu(
    callback: CallbackQuery,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Return to the settings menu, dropping any in-progress edit."""
    await state.clear()
    await callback.answer()
    user, _ = await user_service.ensure_registered(callback.from_user.id)
    await _show_settings_menu(callback.message, user.id, profile_service)


@router.callback_query(SettingsCB.filter(F.action == "edit"))
async def on_edit_field(
    callback: CallbackQuery,
    callback_data: SettingsCB,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Open the choices of a single field."""
    field = callback_data.field
    if field not in EDITABLE_FIELDS:
        await callback.answer("Неизвестная настройка")
        return
    _, profile = await _load_profile(
        callback.from_user.id, user_service, profile_service
    )
    if profile is None:
        await callback.answer("Профиль не найден, начните с /start")
        return
    await callback.answer()

    if field == "topics":
        await state.set_state(SettingsFlow.topics)
        await state.update_data(selected_topics=list(profile.topics))
        await safe_edit(
            callback.message,
            texts.ONBOARDING_PROMPTS["topics"],
            topics_edit_keyboard(set(profile.topics)),
        )
        return
    if field == "timezone":
        await safe_edit(
            callback.message,
            "Выберите часовой пояс:",
            field_options_keyboard("timezone"),
        )
        return
    await safe_edit(
        callback.message,
        texts.ONBOARDING_PROMPTS[field],
        field_options_keyboard(field),
    )


@router.callback_query(SettingsCB.filter(F.action == "apply"))
async def on_apply(
    callback: CallbackQuery,
    callback_data: SettingsCB,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Apply a single-field patch and return to the settings menu."""
    field = callback_data.field
    value = callback_data.value
    if field not in EDITABLE_FIELDS or field == "topics":
        await callback.answer("Неизвестная настройка")
        return
    if field != "timezone" and value not in step_values(field):
        await callback.answer(texts.ONBOARDING_INVALID_ANSWER)
        return
    try:
        patch = ProfileUpdate.model_validate({field: value})
    except ValidationError:
        await callback.answer("Не удалось сохранить значение")
        return
    user, _ = await user_service.ensure_registered(callback.from_user.id)
    try:
        await profile_service.update_profile(user.id, patch)
    except ProfileNotConfiguredError:
        await callback.answer("Профиль не найден, начните с /start")
        return
    except StorageError:
        logger.exception(
            "failed to update field %s for telegram_user_id=%s",
            field,
            callback.from_user.id,
        )
        await callback.answer("Не удалось сохранить, попробуйте позже")
        return
    await callback.answer(texts.SETTINGS_FIELD_SAVED)
    await _show_settings_menu(callback.message, user.id, profile_service)


@router.callback_query(
    SettingsCB.filter(F.action == "topic_toggle"),
    StateFilter(SettingsFlow.topics),
)
async def on_topic_toggle(
    callback: CallbackQuery,
    callback_data: SettingsCB,
    state: FSMContext,
) -> None:
    """Toggle one topic inside the settings topics editor."""
    if callback_data.value not in step_values("topics"):
        await callback.answer(texts.ONBOARDING_INVALID_ANSWER)
        return
    data = await state.get_data()
    selected: list[str] = list(data.get("selected_topics") or [])
    if callback_data.value in selected:
        selected.remove(callback_data.value)
    else:
        selected.append(callback_data.value)
    await state.update_data(selected_topics=selected)
    await callback.answer()
    await safe_edit(
        callback.message,
        texts.ONBOARDING_PROMPTS["topics"],
        topics_edit_keyboard(set(selected)),
    )


@router.callback_query(
    SettingsCB.filter(F.action == "topic_done"),
    StateFilter(SettingsFlow.topics),
)
async def on_topics_done(
    callback: CallbackQuery,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Save the edited topic list and return to the settings menu."""
    data = await state.get_data()
    selected: list[str] = list(data.get("selected_topics") or [])
    patch = ProfileUpdate(topics=selected)
    user, _ = await user_service.ensure_registered(callback.from_user.id)
    try:
        await profile_service.update_profile(user.id, patch)
    except ProfileNotConfiguredError:
        await state.clear()
        await callback.answer("Профиль не найден, начните с /start")
        return
    except StorageError:
        logger.exception(
            "failed to update topics for telegram_user_id=%s",
            callback.from_user.id,
        )
        await callback.answer("Не удалось сохранить, попробуйте позже")
        return
    await state.clear()
    await callback.answer(texts.SETTINGS_FIELD_SAVED)
    await _show_settings_menu(callback.message, user.id, profile_service)


@router.callback_query(SettingsCB.filter(F.action == "tzcustom"))
async def on_timezone_custom(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Switch the timezone editor to free-form text input."""
    await state.set_state(SettingsFlow.timezone_input)
    await callback.answer()
    await safe_edit(callback.message, texts.TIMEZONE_INPUT_PROMPT, None)


@router.message(StateFilter(SettingsFlow.timezone_input), F.text)
async def on_timezone_text(
    message: Message,
    state: FSMContext,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Resolve a typed city or zone and save it as the timezone."""
    resolved = resolve_timezone(message.text or "")
    if resolved is None:
        await message.answer(texts.TIMEZONE_NOT_FOUND.format(query=message.text))
        return
    if message.from_user is None:
        return
    patch = ProfileUpdate(timezone=resolved)
    user, _ = await user_service.ensure_registered(message.from_user.id)
    try:
        await profile_service.update_profile(user.id, patch)
    except ProfileNotConfiguredError:
        await state.clear()
        await message.answer("Профиль не найден, начните с /start")
        return
    except StorageError:
        logger.exception(
            "failed to update timezone for telegram_user_id=%s",
            message.from_user.id,
        )
        await message.answer("Не удалось сохранить, попробуйте позже")
        return
    await state.clear()
    await message.answer(
        f"Часовой пояс: <b>{resolved}</b> — {texts.SETTINGS_FIELD_SAVED}",
        reply_markup=back_to_menu_keyboard(),
    )
