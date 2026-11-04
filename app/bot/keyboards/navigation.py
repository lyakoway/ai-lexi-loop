"""Inline keyboards of the main menu and profile views."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts
from app.bot.keyboards.callbacks import MenuCB, SettingsCB


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Primary navigation offered after /start for configured users."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_OPEN_LESSON,
                    callback_data=MenuCB(action="lesson").pack(),
                ),
                InlineKeyboardButton(
                    text=texts.BTN_OPEN_REVIEW,
                    callback_data=MenuCB(action="review").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_OPEN_PROFILE,
                    callback_data=MenuCB(action="profile").pack(),
                ),
                InlineKeyboardButton(
                    text=texts.BTN_OPEN_SETTINGS,
                    callback_data=MenuCB(action="settings").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_OPEN_HELP,
                    callback_data=MenuCB(action="help").pack(),
                )
            ],
        ]
    )


def start_setup_keyboard() -> InlineKeyboardMarkup:
    """Entry point into onboarding shown to new users."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_START_SETUP,
                    callback_data=SettingsCB(action="onboarding").pack(),
                )
            ]
        ]
    )


def profile_empty_keyboard() -> InlineKeyboardMarkup:
    """Shown under /profile when onboarding has not been completed."""
    return start_setup_keyboard()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Single 'back to menu' button under profile views."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_TO_MENU,
                    callback_data=MenuCB(action="menu").pack(),
                )
            ]
        ]
    )
