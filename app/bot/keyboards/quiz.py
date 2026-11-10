"""Inline keyboards of the active-recall quiz (Stage 6.4.6)."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts


class QuizCB(CallbackData, prefix="qz", sep="~"):
    """Callbacks of the quiz flow (``qz~action``).

    ``level_up`` confirms the CEFR promotion offered after N correct
    answers in a row; the service re-checks the streak, so a stale
    button can never promote undeservedly.
    """

    action: str


def level_up_keyboard() -> InlineKeyboardMarkup:
    """The confirmation button under a level-up offer."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.QUIZ_LEVEL_UP_BUTTON,
                    callback_data=QuizCB(action="level_up").pack(),
                )
            ]
        ]
    )
