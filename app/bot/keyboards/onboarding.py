"""Onboarding step definitions and inline keyboards.

A step is identified by the ProfileCreate field it fills (``step key``).
Options are (value, label) pairs; values are strings and are coerced by
Pydantic schemas when the draft is confirmed.
"""

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts
from app.bot.keyboards.callbacks import OnboardingCB
from app.domain.profile import (
    LESSON_HOUR_CHOICES,
    POPULAR_TIMEZONES,
    LearningGoal,
    Level,
    Topic,
)

#: Order of onboarding steps (field keys of ProfileCreate).
STEP_ORDER: tuple[str, ...] = (
    "native_language",
    "target_language",
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

#: Steps that can be skipped, with the value stored on skip.
SKIP_DEFAULTS: dict[str, Any] = {
    "topics": [],
    "preferred_accent": "us",
}

#: Buttons per row for single-choice steps.
ROW_WIDTHS: dict[str, int] = {
    "native_language": 1,
    "target_language": 1,
    "level": 3,
    "learning_goal": 1,
    "preferred_accent": 1,
    "daily_study_minutes": 2,
    "new_items_per_day": 3,
    "lesson_time": 6,
    "review_time": 6,
    "timezone": 2,
    "topics": 2,
}

#: Options rendered on a full-width row below the grid.
WIDE_OPTIONS: dict[str, set[str]] = {
    "level": {Level.UNKNOWN.value},
}

_MINUTE_CHOICES: tuple[str, ...] = ("5", "10", "15", "30", "45", "60", "90")
_ITEMS_PER_DAY_CHOICES: tuple[str, ...] = ("3", "5", "7", "10", "15", "20")


def options_for_step(step: str) -> list[tuple[str, str]]:
    """Return the (value, label) options offered at the given step."""
    if step == "native_language":
        return [("ru", texts.LANGUAGE_LABELS["ru"])]
    if step == "target_language":
        return [("en", texts.LANGUAGE_LABELS["en"])]
    if step == "level":
        options = [
            (level.value, texts.LEVEL_BUTTON_LABELS[level.value])
            for level in Level
            if level is not Level.UNKNOWN
        ]
        options.append((Level.UNKNOWN.value, "🔍 Не знаю свой уровень"))
        return options
    if step == "learning_goal":
        return [(goal.value, texts.GOAL_LABELS[goal.value]) for goal in LearningGoal]
    if step == "topics":
        return [(topic.value, texts.TOPIC_LABELS[topic.value]) for topic in Topic]
    if step == "preferred_accent":
        return [
            ("uk", texts.ACCENT_LABELS["uk"]),
            ("us", texts.ACCENT_LABELS["us"]),
        ]
    if step == "daily_study_minutes":
        return [(minutes, texts.MINUTES_LABELS[minutes]) for minutes in _MINUTE_CHOICES]
    if step == "new_items_per_day":
        return [(count, count) for count in _ITEMS_PER_DAY_CHOICES]
    if step in ("lesson_time", "review_time"):
        return [(f"{hour:02d}:00", f"{hour:02d}") for hour in LESSON_HOUR_CHOICES]
    if step == "timezone":
        return [
            (zone, zone.rsplit("/", 1)[-1].replace("_", " "))
            for zone in POPULAR_TIMEZONES
        ]
    raise ValueError(f"unknown onboarding step: {step!r}")


def step_values(step: str) -> set[str]:
    """Valid raw values for the step (used to reject stale callbacks)."""
    return {value for value, _ in options_for_step(step)}


def build_grid(
    options: list[tuple[str, str]], step: str, width: int
) -> list[list[InlineKeyboardButton]]:
    wide = WIDE_OPTIONS.get(step, set())
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for value, label in options:
        if value in wide:
            if row:
                rows.append(row)
                row = []
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label,
                        callback_data=OnboardingCB(
                            action="ans", step=step, value=value
                        ).pack(),
                    )
                ]
            )
            continue
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=OnboardingCB(action="ans", step=step, value=value).pack(),
            )
        )
        if len(row) == width:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _navigation_row(step: str) -> list[InlineKeyboardButton]:
    buttons = []
    if STEP_ORDER.index(step) > 0:
        buttons.append(
            InlineKeyboardButton(
                text=texts.BTN_BACK,
                callback_data=OnboardingCB(action="back", step=step).pack(),
            )
        )
    if step in SKIP_DEFAULTS:
        buttons.append(
            InlineKeyboardButton(
                text=texts.BTN_SKIP,
                callback_data=OnboardingCB(action="skip", step=step).pack(),
            )
        )
    buttons.append(
        InlineKeyboardButton(
            text=texts.BTN_CANCEL,
            callback_data=OnboardingCB(action="cancel", step=step).pack(),
        )
    )
    return buttons


def onboarding_step_keyboard(step: str, draft: dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard of one onboarding step.

    ``draft`` is the current FSM data; the topics step uses it to mark
    already selected topics.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if step == "topics":
        selected = set(draft.get("topics") or [])
        for value, label in options_for_step("topics"):
            marker = "✅ " if value in selected else ""
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{marker}{label}",
                        callback_data=OnboardingCB(
                            action="toggle", step="topics", value=value
                        ).pack(),
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.BTN_TOPICS_DONE,
                    callback_data=OnboardingCB(action="next", step="topics").pack(),
                )
            ]
        )
    else:
        rows.extend(build_grid(options_for_step(step), step, ROW_WIDTHS[step]))
        if step == "timezone":
            rows.append(
                [
                    InlineKeyboardButton(
                        text=texts.BTN_TIMEZONE_OTHER,
                        callback_data=OnboardingCB(
                            action="tzother", step="timezone"
                        ).pack(),
                    )
                ]
            )
    rows.append(_navigation_row(step))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard() -> InlineKeyboardMarkup:
    """Keyboard of the onboarding confirmation screen."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_CONFIRM,
                    callback_data=OnboardingCB(action="confirm", step="confirm").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_ONBOARDING_RESTART,
                    callback_data=OnboardingCB(action="restart", step="confirm").pack(),
                ),
                InlineKeyboardButton(
                    text=texts.BTN_CANCEL,
                    callback_data=OnboardingCB(action="cancel", step="confirm").pack(),
                ),
            ],
        ]
    )
