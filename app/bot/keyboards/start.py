"""Keyboards shown with the ``/start`` greeting."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts


def setup_profile_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard with the entry point to profile setup."""
    setup_button = InlineKeyboardButton(
        text=texts.SETUP_BUTTON,
        callback_data=texts.CALLBACK_SETUP_PROFILE,
    )
    return InlineKeyboardMarkup(inline_keyboard=[[setup_button]])
