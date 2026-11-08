"""Inline keyboards of the /generate flow."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.bot import texts
from app.domain.profile import Topic


class GenCB(CallbackData, prefix="gen", sep="~"):
    """Callbacks of the generation flow (``gen~action~token``).

    ``token`` is the id of the first draft in the saved set; handlers
    compare it with the FSM state, so stale presses are rejected without
    touching the drafts.
    """

    action: str
    token: int = 0


def topic_keyboard() -> InlineKeyboardMarkup:
    """One topic per row plus the «any topic» wildcard."""
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=GenCB(action="topic", token=value_index).pack(),
            )
        ]
        for value_index, label in enumerate(
            [texts.TOPIC_LABELS[topic.value] for topic in Topic]
        )
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.GEN_ANY_TOPIC,
                callback_data=GenCB(action="topic", token=-1).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def count_keyboard() -> InlineKeyboardMarkup:
    """Row of counts plus a cancel escape."""
    rows = [
        [
            InlineKeyboardButton(
                text=count,
                callback_data=GenCB(action="count", token=int(count)).pack(),
            )
            for count in texts.GEN_COUNT_CHOICES
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def situation_keyboard() -> InlineKeyboardMarkup:
    """Skip button for the optional situation step."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.GEN_SKIP,
                    callback_data=GenCB(action="skip_situation").pack(),
                )
            ]
        ]
    )


def preview_keyboard(token: int) -> InlineKeyboardMarkup:
    """Accept/reject actions under the saved draft preview."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.GEN_ACCEPT,
                    callback_data=GenCB(action="accept", token=token).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.GEN_REJECT,
                    callback_data=GenCB(action="reject", token=token).pack(),
                )
            ],
        ]
    )


def topic_value_at(index: int) -> str:
    """Map a callback token index back to a topic value (-1 = any)."""
    topics = [topic.value for topic in Topic]
    if index == -1:
        return "any"
    if 0 <= index < len(topics):
        return topics[index]
    return topics[0]
