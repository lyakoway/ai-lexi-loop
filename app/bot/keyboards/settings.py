"""Inline keyboards of the settings editor."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts
from app.bot.keyboards.callbacks import MenuCB, SettingsCB
from app.bot.keyboards.onboarding import (
    ROW_WIDTHS,
    build_grid,
    options_for_step,
)

#: Fields editable one at a time, in menu display order.
EDITABLE_FIELDS: tuple[str, ...] = (
    "level",
    "learning_goal",
    "topics",
    "preferred_accent",
    "daily_study_minutes",
    "new_items_per_day",
    "lesson_time",
    "review_time",
    "timezone",
)


def settings_menu_keyboard() -> InlineKeyboardMarkup:
    """Menu listing editable settings plus full onboarding re-run."""
    rows = []
    pair: list[InlineKeyboardButton] = []
    for field in EDITABLE_FIELDS:
        pair.append(
            InlineKeyboardButton(
                text=texts.FIELD_LABELS[field],
                callback_data=SettingsCB(action="edit", field=field).pack(),
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_RERUN_ONBOARDING,
                callback_data=SettingsCB(action="onboarding").pack(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_TO_MENU,
                callback_data=MenuCB(action="menu").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def field_options_keyboard(field: str) -> InlineKeyboardMarkup:
    """Keyboard with the choices of a single setting."""
    if field == "timezone":
        rows = build_grid(options_for_step("timezone"), "timezone", 2)
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.BTN_TIMEZONE_OTHER,
                    callback_data=SettingsCB(
                        action="tzcustom", field="timezone"
                    ).pack(),
                )
            ]
        )
    else:
        rows = build_grid(options_for_step(field), field, ROW_WIDTHS[field])
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_BACK,
                callback_data=SettingsCB(action="menu").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topics_edit_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    """Topic toggles for the settings editor with current marks."""
    rows = []
    for value, label in options_for_step("topics"):
        marker = "✅ " if value in selected else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{label}",
                    callback_data=SettingsCB(
                        action="topic_toggle", field="topics", value=value
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_TOPICS_DONE,
                callback_data=SettingsCB(action="topic_done", field="topics").pack(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_CANCEL,
                callback_data=SettingsCB(action="menu").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
