"""Tests for profile and draft rendering."""

from datetime import date, time

from app.bot.profile_view import format_draft, format_profile, settings_menu_text
from app.db.models import Profile


def make_profile() -> Profile:
    return Profile(
        id=1,
        user_id=2,
        native_language="ru",
        target_language="en",
        level="B1",
        learning_goal="work",
        topics=["work", "technology"],
        preferred_accent="uk",
        daily_study_minutes=15,
        new_items_per_day=5,
        lesson_time=time(8, 0),
        review_time=time(19, 0),
        timezone="Asia/Novosibirsk",
        created_at=date(2026, 11, 3),
        updated_at=date(2026, 11, 3),
    )


def test_format_profile_shows_every_field_label() -> None:
    rendered = format_profile(make_profile())
    assert "Профиль обучения" in rendered
    assert "Уровень" in rendered and "B1" in rendered
    assert "Работа" in rendered
    assert "Технологии" in rendered
    assert "Британский" in rendered
    assert "08:00" in rendered and "19:00" in rendered
    assert "Asia/Novosibirsk" in rendered
    assert "15" in rendered and "5" in rendered


def test_format_profile_renders_unknown_level_friendly() -> None:
    profile = make_profile()
    profile.level = "unknown"
    assert "определим по ходу" in format_profile(profile)


def test_format_profile_renders_empty_topics() -> None:
    profile = make_profile()
    profile.topics = []
    assert "не выбраны" in format_profile(profile)


def test_format_draft_shows_confirmation_title() -> None:
    draft = {
        "native_language": "ru",
        "target_language": "en",
        "level": "unknown",
        "learning_goal": "travel",
        "topics": [],
        "preferred_accent": "us",
        "daily_study_minutes": "30",
        "new_items_per_day": "7",
        "lesson_time": "09:00",
        "review_time": "20:00",
        "timezone": "Europe/Moscow",
    }
    rendered = format_draft(draft)
    assert "Проверьте настройки" in rendered
    assert "Путешествия" in rendered
    assert "не выбраны" in rendered


def test_settings_menu_lists_editable_fields_with_values() -> None:
    rendered = settings_menu_text(make_profile())
    assert "Настройки обучения" in rendered
    assert "Часовой пояс" in rendered
    assert "Время урока" in rendered
    assert "изменить" in rendered
