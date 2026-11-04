"""Schemas of the learning profile.

``ProfileCreate`` describes a complete profile (the result of onboarding
or a full re-run); ``ProfileUpdate`` carries a partial patch for single
setting edits. All validation happens here, so services and repositories
can trust the data.
"""

from datetime import time
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.profile import Accent, LearningGoal, Level, Topic

LanguageCode = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,8}$")]


def _check_timezone(value: str) -> str:
    """Ensure the value is a valid IANA zone name."""
    try:
        zone = ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError(f"unknown timezone: {value!r}") from error
    return zone.key


TimezoneName = Annotated[str, AfterValidator(_check_timezone)]


class ProfileBase(BaseModel):
    """Fields shared by create and full-update payloads."""

    model_config = ConfigDict(extra="forbid")

    native_language: LanguageCode
    target_language: LanguageCode
    level: Level
    learning_goal: LearningGoal
    topics: list[Topic]
    preferred_accent: Accent
    daily_study_minutes: int = Field(ge=5, le=120)
    new_items_per_day: int = Field(ge=1, le=20)
    lesson_time: time
    review_time: time
    timezone: TimezoneName

    @field_validator("topics")
    @classmethod
    def _deduplicate_topics(cls, value: list[Topic]) -> list[Topic]:
        """Drop duplicate topics keeping the order of first appearance."""
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _languages_differ(self) -> "ProfileBase":
        if self.native_language == self.target_language:
            raise ValueError("native and target languages must differ")
        return self


class ProfileCreate(ProfileBase):
    """A complete profile as confirmed by the user."""


class ProfileUpdate(BaseModel):
    """A partial profile patch: only the fields being changed.

    ``topics`` is replaced as a whole list; editing one topic means
    sending the new complete list.
    """

    model_config = ConfigDict(extra="forbid")

    native_language: LanguageCode | None = None
    target_language: LanguageCode | None = None
    level: Level | None = None
    learning_goal: LearningGoal | None = None
    topics: list[Topic] | None = None
    preferred_accent: Accent | None = None
    daily_study_minutes: int | None = Field(default=None, ge=5, le=120)
    new_items_per_day: int | None = Field(default=None, ge=1, le=20)
    lesson_time: time | None = None
    review_time: time | None = None
    timezone: TimezoneName | None = None

    @field_validator("topics")
    @classmethod
    def _deduplicate_topics(cls, value: list[Topic] | None) -> list[Topic] | None:
        if value is None:
            return None
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "ProfileUpdate":
        if not self.model_dump(exclude_unset=True):
            raise ValueError("at least one field is required")
        return self
