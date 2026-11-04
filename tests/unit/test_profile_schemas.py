"""Tests for profile schemas and domain value resolution."""

import pytest
from pydantic import ValidationError

from app.domain.profile import resolve_timezone
from app.schemas.profile import ProfileCreate, ProfileUpdate


def valid_payload() -> dict[str, str | list[str]]:
    """A minimal valid onboarding draft (raw string values)."""
    return {
        "native_language": "ru",
        "target_language": "en",
        "level": "B1",
        "learning_goal": "work",
        "topics": ["work", "technology"],
        "preferred_accent": "uk",
        "daily_study_minutes": "15",
        "new_items_per_day": "5",
        "lesson_time": "08:00",
        "review_time": "19:00",
        "timezone": "Europe/Moscow",
    }


def test_profile_create_accepts_valid_draft() -> None:
    profile = ProfileCreate.model_validate(valid_payload())
    assert profile.level.value == "B1"
    assert profile.daily_study_minutes == 15
    assert str(profile.lesson_time) == "08:00:00"
    assert [topic.value for topic in profile.topics] == ["work", "technology"]


@pytest.mark.parametrize(
    "field",
    [
        "level",
        "learning_goal",
        "preferred_accent",
    ],
)
def test_profile_create_rejects_unknown_enum(field: str) -> None:
    payload = valid_payload()
    payload[field] = "definitely-not-a-value"
    with pytest.raises(ValidationError):
        ProfileCreate.model_validate(payload)


def test_profile_create_rejects_same_languages() -> None:
    payload = valid_payload()
    payload["native_language"] = "en"
    with pytest.raises(ValidationError, match="must differ"):
        ProfileCreate.model_validate(payload)


def test_profile_create_rejects_unknown_timezone() -> None:
    payload = valid_payload()
    payload["timezone"] = "Mars/Olympus_Mons"
    with pytest.raises(ValidationError, match="unknown timezone"):
        ProfileCreate.model_validate(payload)


def test_profile_create_rejects_timezone_offset_only() -> None:
    """A bare UTC offset is not a valid zone representation."""
    payload = valid_payload()
    payload["timezone"] = "UTC+3"
    with pytest.raises(ValidationError):
        ProfileCreate.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("daily_study_minutes", "3"),
        ("daily_study_minutes", "121"),
        ("new_items_per_day", "0"),
        ("new_items_per_day", "21"),
    ],
)
def test_profile_create_rejects_out_of_range_numbers(field: str, value: str) -> None:
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        ProfileCreate.model_validate(payload)


def test_profile_create_deduplicates_topics() -> None:
    payload = valid_payload()
    payload["topics"] = ["work", "travel", "work"]
    profile = ProfileCreate.model_validate(payload)
    assert [topic.value for topic in profile.topics] == ["work", "travel"]


def test_profile_create_accepts_empty_topics() -> None:
    payload = valid_payload()
    payload["topics"] = []
    assert ProfileCreate.model_validate(payload).topics == []


def test_profile_create_forbids_extra_fields() -> None:
    payload = {**valid_payload(), "hacker_field": 1}
    with pytest.raises(ValidationError):
        ProfileCreate.model_validate(payload)


def test_profile_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        ProfileUpdate.model_validate({})


def test_profile_update_partial_patch() -> None:
    patch = ProfileUpdate.model_validate({"level": "A2"})
    assert patch.level is not None and patch.level.value == "A2"
    assert patch.timezone is None


def test_profile_update_validates_timezone() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate.model_validate({"timezone": "not/a/zone"})


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Europe/Moscow", "Europe/Moscow"),
        ("москва", "Europe/Moscow"),
        ("МСК", "Europe/Moscow"),
        ("новосибирск", "Asia/Novosibirsk"),
        ("berlin", "Europe/Berlin"),
        ("Нью-Йорк", "America/New_York"),
    ],
)
def test_resolve_timezone_matches_zones_and_cities(query: str, expected: str) -> None:
    assert resolve_timezone(query) == expected


def test_resolve_timezone_rejects_unknown_input() -> None:
    assert resolve_timezone("марс") is None
    assert resolve_timezone("") is None
    assert resolve_timezone("   ") is None
