"""Unit tests for the Russian hint fading rule and accent selection."""

from datetime import UTC, datetime, timedelta

from app.bot.lesson_view import (
    phonetic_hint_for,
    should_show_hint,
)
from app.db.models import Expression

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
FADE_WEEKS = 8


def is_hint_shown(
    *,
    age_weeks: float,
    is_difficult: bool,
) -> bool:
    created_at = NOW - timedelta(weeks=age_weeks)
    return should_show_hint(
        created_at=created_at,
        is_difficult=is_difficult,
        now=NOW,
        fade_after_weeks=FADE_WEEKS,
    )


def make_expression(
    *,
    base: str,
    us: str | None = None,
    uk: str | None = None,
) -> Expression:
    """An in-memory Expression with only the hint columns filled."""
    return Expression(
        phonetic_hint_ru=base,
        phonetic_hint_ru_us=us,
        phonetic_hint_ru_uk=uk,
    )


def test_fresh_word_shows_hint() -> None:
    assert is_hint_shown(age_weeks=1, is_difficult=False)


def test_word_at_fade_boundary_without_difficulty_hides_hint() -> None:
    """Older than N weeks and not difficult → the hint is gone."""
    assert not is_hint_shown(age_weeks=FADE_WEEKS + 1, is_difficult=False)


def test_old_but_difficult_word_keeps_hint() -> None:
    assert is_hint_shown(age_weeks=FADE_WEEKS + 1, is_difficult=True)


def test_word_just_before_boundary_keeps_hint() -> None:
    created_at = NOW - timedelta(weeks=FADE_WEEKS) + timedelta(hours=1)
    assert should_show_hint(
        created_at=created_at,
        is_difficult=False,
        now=NOW,
        fade_after_weeks=FADE_WEEKS,
    )


def test_accent_hint_selection_prefers_accent_column() -> None:
    """For words where US/UK pronunciations differ, the Russian
    transcription differs too (accent-aware, not mechanical)."""
    expression = make_expression(
        base="лэтс скЭджул э мИтинг",
        us="лэтс скЭджул э мИтинг",
        uk="лэтс шэдьюл э мИтинг",
    )
    assert phonetic_hint_for(expression, "us") == "лэтс скЭджул э мИтинг"
    assert phonetic_hint_for(expression, "uk") == "лэтс шэдьюл э мИтинг"


def test_accent_hint_falls_back_to_base_when_variant_missing() -> None:
    expression = make_expression(base="гуд мОрнинг")
    assert phonetic_hint_for(expression, "us") == "гуд мОрнинг"
    assert phonetic_hint_for(expression, "uk") == "гуд мОрнинг"


def test_accent_hint_without_accent_returns_base() -> None:
    expression = make_expression(base="база", us="вариант us", uk="вариант uk")
    assert phonetic_hint_for(expression, None) == "база"
