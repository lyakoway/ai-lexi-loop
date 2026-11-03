"""Handlers for ``/start`` and the profile-setup entry point."""

import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.keyboards.start import setup_profile_keyboard
from app.repositories import StorageError
from app.services import UserService

logger = logging.getLogger(__name__)

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, user_service: UserService) -> None:
    """Greet the user and register their account.

    A storage failure must not leave the user without a reply: the greeting
    is still sent, with a note that the profile could not be saved.
    """
    greeting = texts.START_GREETING
    telegram_user_id = message.from_user.id if message.from_user else None
    if telegram_user_id is None:
        # Telegram private chats always have from_user, but channel posts
        # may not; nothing to register in that case.
        logger.warning("start command without from_user, skip registration")
    else:
        try:
            await user_service.ensure_registered(telegram_user_id)
        except StorageError:
            logger.exception(
                "failed to register user telegram_user_id=%s", telegram_user_id
            )
            greeting = texts.START_GREETING_WITH_REGISTRATION_WARNING.format(
                greeting=greeting
            )
    await message.answer(
        greeting,
        reply_markup=setup_profile_keyboard(),
    )


@router.callback_query(F.data == texts.CALLBACK_SETUP_PROFILE)
async def on_setup_profile(callback: CallbackQuery) -> None:
    """Answer the setup button with a preview of the upcoming flow."""
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(texts.SETUP_TEASER)
    else:
        logger.warning("setup callback without message, nothing to answer to")
