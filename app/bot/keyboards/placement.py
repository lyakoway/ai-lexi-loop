"""Inline keyboards of the level-placement diagnostic (Stage 6.4.7)."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts


class PlacementCB(CallbackData, prefix="plc", sep="~"):
    """Callbacks of the placement flow (``plc~action~index~value``).

    ``index`` is the question the buttons were rendered for; presses
    with a stale index are rejected instead of silently scoring twice.
    ``value`` is the zero-based option index.
    """

    action: str
    index: int = 0
    value: int = 0


def placement_question_keyboard(
    *, index: int, options: tuple[str, ...]
) -> InlineKeyboardMarkup:
    """Option buttons of one question, one per row (long options)."""
    rows = [
        [
            InlineKeyboardButton(
                text=option,
                callback_data=PlacementCB(
                    action="ans", index=index, value=option_index
                ).pack(),
            )
        ]
        for option_index, option in enumerate(options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def placement_result_keyboard(level: str) -> InlineKeyboardMarkup:
    """Confirm the recommendation or fall back to the manual choice."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_CONFIRM_LEVEL.format(level=level),
                    callback_data=PlacementCB(action="confirm").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_PICK_MANUAL,
                    callback_data=PlacementCB(action="manual").pack(),
                )
            ],
        ]
    )
