"""Inline keyboards of the AI tutor views.

The explain button is shared by the lesson and review flows; the
callback carries the card position it was rendered for, so stale
presses are rejected the same way lesson and review buttons are.
"""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.bot import texts


class TutorCB(CallbackData, prefix="tut", sep="~"):
    """Callbacks of tutor views (``tut~action~token~index``).

    ``token``/``index`` identify the card the view was opened from; the
    handler compares them with the active lesson/review FSM state.
    """

    action: str
    token: int
    index: int


def explain_button(token: int, index: int) -> InlineKeyboardButton:
    """The card action that opens the AI explanation."""
    return InlineKeyboardButton(
        text=texts.BTN_EXPLAIN,
        callback_data=TutorCB(action="explain", token=token, index=index).pack(),
    )


def back_to_card_keyboard(token: int, index: int) -> InlineKeyboardMarkup:
    """Keyboard under an explanation view: return to the card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_BACK_TO_CARD,
                    callback_data=TutorCB(
                        action="back", token=token, index=index
                    ).pack(),
                )
            ]
        ]
    )
