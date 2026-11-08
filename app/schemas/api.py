"""Request/response schemas of the public HTTP API (v1).

These DTOs decouple the API contract from ORM models: Telegram renders
its own views, Web renders these.
"""

from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.services.tutor.chat_outputs import ChatReplyOut


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


class GenerateIn(BaseModel):
    """Request of personal expression generation."""

    topic: str = Field(min_length=1, max_length=64)
    count: int = Field(default=5, ge=1, le=7)
    situation: str = Field(default="", max_length=300)


class GeneratedDraftOut(BaseModel):
    """One draft awaiting the user's decision."""

    model_config = ConfigDict(from_attributes=True)

    draft_id: int
    text: str
    translation: str
    ipa: str
    phonetic_hint_ru: str
    example: str
    example_translation: str
    level: str
    topic: str


class GeneratedOut(BaseModel):
    """Generation result with the review set and dedup statistics."""

    drafts: list[GeneratedDraftOut]
    duplicates_skipped: int
    unavailable_reason: str | None = None


class AcceptIn(BaseModel):
    """Draft ids to accept into learning."""

    draft_ids: list[int] = Field(min_length=1, max_length=20)


class AcceptOut(BaseModel):
    """Acceptance result: catalogue cards now in the user's learning."""

    accepted_expression_ids: list[int]
    skipped_draft_ids: list[int]


class ChatConversationOut(BaseModel):
    """A tutor chat dialog as visible to its owner.

    Internal claim columns (generation_started_at / generation_claim_id /
    last_generation_at) are deliberately excluded: they are scheduling
    internals, not user-facing data.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    language: str
    native_language: str
    level: str
    topic: str | None
    situation: str | None
    status: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


class ChatMessageOut(BaseModel):
    """One tutor chat message.

    ``in_reply_to_message_id`` preserves the assistant → user link;
    ``client_request_id`` echoes the caller's Idempotency-Key for user
    messages. Internal fields (prompt_version, error_kind) are excluded.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: Literal["user", "assistant"]
    text: str
    client_request_id: str | None
    in_reply_to_message_id: int | None
    created_at: datetime


class ChatHistoryOut(BaseModel):
    """Messages of one dialog, chronological (oldest first)."""

    conversation_id: int
    messages: list[ChatMessageOut]


class ChatSendOut(BaseModel):
    """Result of sending one message: the saved pair and the reply."""

    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    reply: ChatReplyOut
    conversation_status: Literal["active", "closed"]


class ChatStartIn(BaseModel):
    """Optional parameters when starting a tutor chat dialog."""

    topic: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)] = ""
    situation: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=300)
    ] = ""


class ChatMessageIn(BaseModel):
    """One user message for the tutor chat (validated before the LLM)."""

    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]
