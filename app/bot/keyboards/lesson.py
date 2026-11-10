"""Inline keyboards of an in-progress lesson."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts
from app.bot.keyboards.tutor import explain_button, listen_button


class LessonCB(CallbackData, prefix="lssn", sep="~"):
    """Callbacks of lesson navigation (``lssn~action~token~index``).

    ``token`` is the id of the lesson's first expression and identifies
    the lesson; ``index`` is the card the button belongs to. The handler
    compares both with the current FSM state, so double presses and
    buttons of old lessons are rejected instead of corrupting progress.
    """

    action: str
    token: int
    index: int


def lesson_card_keyboard(
    *,
    token: int,
    index: int,
    total: int,
    expression_id: int,
    with_audio: bool = False,
) -> InlineKeyboardMarkup:
    """Keyboard under one card.

    The last card offers only finishing — there is no next card.
    """
    next_button = InlineKeyboardButton(
        text=texts.BTN_NEXT_CARD,
        callback_data=LessonCB(action="next", token=token, index=index).pack(),
    )
    finish_button = InlineKeyboardButton(
        text=texts.BTN_FINISH_LESSON,
        callback_data=LessonCB(action="finish", token=token, index=index).pack(),
    )
    rows = [[finish_button]] if index >= total - 1 else [[next_button], [finish_button]]
    audio_button = [listen_button(expression_id=expression_id)] if with_audio else []
    rows.extend([audio_button] if audio_button else [])
    rows.append([explain_button(token=token, index=index)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
