"""Request/response schemas of the public HTTP API (v1).

These DTOs decouple the API contract from ORM models: Telegram renders
its own views, Web renders these.
"""

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProfileOut(BaseModel):
    """The user's learning profile as seen by API clients."""

    model_config = ConfigDict(from_attributes=True)

    native_language: str
    target_language: str
    level: str
    learning_goal: str
    topics: list[str]
    preferred_accent: str
    daily_study_minutes: int
    new_items_per_day: int
    lesson_time: time
    review_time: time
    timezone: str


class LessonCardOut(BaseModel):
    """One expression card inside a lesson or review session."""

    expression_id: int
    text: str
    translation: str
    ipa: str
    phonetic_hint_ru: str
    example: str
    example_translation: str
    level: str
    topic: str


class LessonOut(BaseModel):
    """A freshly built lesson (or the reason none could be built)."""

    lesson_ref: str = ""
    used_default_level: bool = False
    expressions: list[LessonCardOut] = Field(default_factory=list)
    unavailable_reason: str | None = None


class DueReviewsOut(BaseModel):
    """Cards whose review is due."""

    cards: list[LessonCardOut]


class ReviewAnswerIn(BaseModel):
    """One spaced-repetition answer."""

    remembered: bool


class ReviewAnswerOut(BaseModel):
    """Scheduling state after an answer."""

    expression_id: int
    review_count: int
    interval_days: int
    ease_factor: float
    next_review_at: datetime


class StatsOverviewOut(BaseModel):
    """Headline learning stats."""

    expressions_shown: int
    expressions_completed: int
    lessons_completed: int
    reviews_total: int
    remember_ratio: float | None
    due_count: int
    streak_days: int


class DayActivityOut(BaseModel):
    """One day of the activity chart."""

    day: date
    expressions_shown: int
    reviews: int


class StatsProgressOut(BaseModel):
    """Daily activity for the recent period."""

    days: list[DayActivityOut]


LessonUnavailableReason = Literal[
    "no_expressions_for_topics",
    "no_expressions_for_level",
    "all_served",
]
