"""Inline keyboards of an in-progress review session."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts
from app.bot.keyboards.tutor import explain_button, listen_button


class ReviewCB(CallbackData, prefix="rvw", sep="~"):
    """Callbacks of review answers (``rvw~action~token~index``).

    ``token`` is the id of the session's first expression; ``index`` is
    the card the buttons belong to. Both are compared with the current
    FSM state, so double presses and buttons of old sessions are
    rejected instead of double-applying an answer.
    """

    action: str
    token: int
    index: int


def review_card_keyboard(
    *, token: int, index: int, expression_id: int, with_audio: bool = False
) -> InlineKeyboardMarkup:
    """The two answer buttons under one review card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_REMEMBER,
                    callback_data=ReviewCB(
                        action="remember", token=token, index=index
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=texts.BTN_FORGOT,
                    callback_data=ReviewCB(
                        action="forgot", token=token, index=index
                    ).pack(),
                ),
            ],
            [explain_button(token=token, index=index)],
            [listen_button(expression_id=expression_id)] if with_audio else [],
        ]
    )
