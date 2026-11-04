"""Formatting of profiles and onboarding drafts into Telegram HTML.

Pure presentation: takes plain values (the ORM model or the FSM draft
dict) and renders the text shown to the user.
"""

from typing import Any

from app.bot import texts
from app.db.models import Profile
from app.domain.profile import LESSON_HOUR_CHOICES, POPULAR_TIMEZONES


def _label(mapping: dict[str, str], value: Any) -> str:
    return mapping.get(str(value), str(value))


def _timezone_label(zone: str) -> str:
    if zone in POPULAR_TIMEZONES:
        return zone
    return zone.replace("_", " ")


def _value_text(field: str, value: Any) -> str:
    """Human-readable representation of one profile field value."""
    if field == "native_language" or field == "target_language":
        return _label(texts.LANGUAGE_LABELS, value)
    if field == "level":
        return _label(texts.LEVEL_VIEW_LABELS, value)
    if field == "learning_goal":
        return _label(texts.GOAL_LABELS, value)
    if field == "topics":
        if not value:
            return texts.NO_TOPICS_SELECTED
        return ", ".join(_label(texts.TOPIC_LABELS, topic) for topic in value)
    if field == "preferred_accent":
        return _label(texts.ACCENT_LABELS, value)
    if field == "daily_study_minutes":
        return _label(texts.MINUTES_LABELS, value)
    if field == "timezone":
        return _timezone_label(str(value))
    if field == "lesson_time" or field == "review_time":
        return str(value)
    return str(value)


def _format_fields(fields: dict[str, Any]) -> str:
    lines = []
    for field, label in texts.FIELD_LABELS.items():
        if field in fields:
            lines.append(f"{label}: <b>{_value_text(field, fields[field])}</b>")
    return "\n".join(lines)


def format_profile(profile: Profile) -> str:
    """Render a stored profile for the /profile command."""
    return f"{texts.PROFILE_TITLE}\n\n" + _format_fields(
        {
            "native_language": profile.native_language,
            "target_language": profile.target_language,
            "level": profile.level,
            "learning_goal": profile.learning_goal,
            "topics": list(profile.topics),
            "preferred_accent": profile.preferred_accent,
            "daily_study_minutes": profile.daily_study_minutes,
            "new_items_per_day": profile.new_items_per_day,
            "lesson_time": profile.lesson_time.strftime("%H:%M"),
            "review_time": profile.review_time.strftime("%H:%M"),
            "timezone": profile.timezone,
        }
    )


def format_draft(draft: dict[str, Any]) -> str:
    """Render the onboarding draft for the confirmation screen."""
    return texts.ONBOARDING_CONFIRM_TITLE + "\n" + _format_fields(draft)


def settings_menu_text(profile: Profile) -> str:
    """Render the settings menu with current values."""
    return (
        texts.SETTINGS_MENU_TITLE
        + "\n"
        + _format_fields(
            {
                "native_language": profile.native_language,
                "target_language": profile.target_language,
                "level": profile.level,
                "learning_goal": profile.learning_goal,
                "topics": list(profile.topics),
                "preferred_accent": profile.preferred_accent,
                "daily_study_minutes": profile.daily_study_minutes,
                "new_items_per_day": profile.new_items_per_day,
                "lesson_time": profile.lesson_time.strftime("%H:%M"),
                "review_time": profile.review_time.strftime("%H:%M"),
                "timezone": profile.timezone,
            }
        )
        + "\n\nВыберите, что изменить:"
    )


def topics_summary(selected: list[str]) -> str:
    """One-line summary of the currently selected topics."""
    return _value_text("topics", selected)


def hours_range() -> str:
    """Human-readable range of offered lesson hours (used in prompts)."""
    return f"{LESSON_HOUR_CHOICES[0]:02d}:00–{LESSON_HOUR_CHOICES[-1]:02d}:00"
