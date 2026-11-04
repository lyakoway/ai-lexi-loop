"""Navigation commands and main-menu callbacks."""

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards.callbacks import MenuCB
from app.bot.keyboards.navigation import (
    back_to_menu_keyboard,
    main_menu_keyboard,
    profile_empty_keyboard,
)
from app.bot.keyboards.settings import settings_menu_keyboard
from app.bot.messages import safe_edit
from app.bot.profile_view import format_profile, settings_menu_text
from app.db.models import Profile, User
from app.repositories import StorageError
from app.services import ProfileService, UserService

logger = logging.getLogger(__name__)

router = Router(name="navigation")


async def resolve_user_and_profile(
    telegram_user_id: int,
    user_service: UserService,
    profile_service: ProfileService,
) -> tuple[User, Profile | None]:
    """Load (or register) the user and their profile for a command."""
    user, _ = await user_service.ensure_registered(telegram_user_id)
    profile = await profile_service.get_profile(user.id)
    return user, profile


@router.message(Command("profile"))
async def cmd_profile(
    message: Message,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Show the stored profile, or invite to onboarding."""
    if message.from_user is None:
        return
    try:
        _, profile = await resolve_user_and_profile(
            message.from_user.id, user_service, profile_service
        )
    except StorageError:
        logger.exception(
            "failed to load profile, telegram_user_id=%s", message.from_user.id
        )
        await message.answer(
            "Не удалось загрузить профиль — сервис временно недоступен."
        )
        return
    if profile is None:
        await message.answer(texts.PROFILE_EMPTY, reply_markup=profile_empty_keyboard())
        return
    await message.answer(format_profile(profile), reply_markup=back_to_menu_keyboard())


@router.message(Command("settings"))
async def cmd_settings(
    message: Message,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Open the settings editor (requires a configured profile)."""
    if message.from_user is None:
        return
    try:
        _, profile = await resolve_user_and_profile(
            message.from_user.id, user_service, profile_service
        )
    except StorageError:
        logger.exception(
            "failed to load profile, telegram_user_id=%s", message.from_user.id
        )
        await message.answer(
            "Не удалось открыть настройки — сервис временно недоступен."
        )
        return
    if profile is None:
        await message.answer(
            texts.PROFILE_EMPTY_FOR_SETTINGS, reply_markup=profile_empty_keyboard()
        )
        return
    await message.answer(
        settings_menu_text(profile), reply_markup=settings_menu_keyboard()
    )


@router.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Abort any active onboarding or settings flow."""
    if await state.get_state() is None:
        await message.answer("Сейчас нечего отменять 🙂")
        return
    await state.clear()
    await message.answer(texts.ONBOARDING_CANCELLED, reply_markup=main_menu_keyboard())


@router.callback_query(MenuCB.filter(F.action == "menu"))
async def on_menu_open(callback: CallbackQuery) -> None:
    """Show the main menu text under the current view."""
    await callback.answer()
    await safe_edit(callback.message, texts.MAIN_MENU_TEXT, main_menu_keyboard())


@router.callback_query(MenuCB.filter(F.action == "profile"))
async def on_menu_profile(
    callback: CallbackQuery,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Show the profile from the main-menu button."""
    await callback.answer()
    if callback.message is None:
        return
    try:
        _, profile = await resolve_user_and_profile(
            callback.from_user.id, user_service, profile_service
        )
    except StorageError:
        await callback.message.answer(
            "Не удалось загрузить профиль — сервис временно недоступен."
        )
        return
    if profile is None:
        await safe_edit(callback.message, texts.PROFILE_EMPTY, profile_empty_keyboard())
        return
    await safe_edit(callback.message, format_profile(profile), back_to_menu_keyboard())


@router.callback_query(MenuCB.filter(F.action == "settings"))
async def on_menu_settings(
    callback: CallbackQuery,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Open the settings editor from the main-menu button."""
    await callback.answer()
    if callback.message is None:
        return
    try:
        _, profile = await resolve_user_and_profile(
            callback.from_user.id, user_service, profile_service
        )
    except StorageError:
        await callback.message.answer(
            "Не удалось открыть настройки — сервис временно недоступен."
        )
        return
    if profile is None:
        await safe_edit(
            callback.message,
            texts.PROFILE_EMPTY_FOR_SETTINGS,
            profile_empty_keyboard(),
        )
        return
    await safe_edit(
        callback.message, settings_menu_text(profile), settings_menu_keyboard()
    )


@router.callback_query(MenuCB.filter(F.action == "help"))
async def on_menu_help(callback: CallbackQuery) -> None:
    """Show the help text from the main-menu button."""
    await callback.answer()
    await safe_edit(callback.message, texts.HELP_TEXT, back_to_menu_keyboard())
