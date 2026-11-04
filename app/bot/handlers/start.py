"""Handler for ``/start``: greeting and routing by profile state."""

import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot import texts
from app.bot.keyboards.navigation import (
    main_menu_keyboard,
    start_setup_keyboard,
)
from app.repositories import StorageError
from app.services import ProfileService, UserService

logger = logging.getLogger(__name__)

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    user_service: UserService,
    profile_service: ProfileService,
) -> None:
    """Greet the user and route them by onboarding state.

    New users are invited into onboarding; configured users get the main
    menu. A storage failure must not leave the user without a reply.
    """
    if message.from_user is None:
        return
    telegram_user_id = message.from_user.id
    greeting = texts.START_GREETING_NEW
    keyboard = start_setup_keyboard()
    try:
        user, _ = await user_service.ensure_registered(telegram_user_id)
        profile = await profile_service.get_profile(user.id)
    except StorageError:
        logger.exception("failed to load user telegram_user_id=%s", telegram_user_id)
        await message.answer(
            texts.START_GREETING_WITH_REGISTRATION_WARNING.format(greeting=greeting),
            reply_markup=keyboard,
        )
        return
    if profile is not None:
        await message.answer(
            texts.START_GREETING_EXISTING, reply_markup=main_menu_keyboard()
        )
        return
    await message.answer(greeting, reply_markup=keyboard)
