"""Helpers for working with Telegram message objects in handlers."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InaccessibleMessage, InlineKeyboardMarkup, Message


async def safe_edit(
    message: Message | InaccessibleMessage | None,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit a message, tolerating Telegram's not-modified error.

    Callbacks can arrive on messages that are too old to edit
    (``InaccessibleMessage``) or without a message at all; in both cases
    there is nothing to edit, so the call is skipped.
    """
    if not isinstance(message, Message):
        return
    try:
        await message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise
