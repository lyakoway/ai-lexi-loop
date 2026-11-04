"""Lesson building: pick new expressions for a user and record progress.

Selection strategy (deliberate and documented, no hidden substitution):

- Only active expressions in the profile's target language are served.
- The level is the profile level. When the level is ``unknown``, lessons
  start from A2 — an explicit default that will be replaced by the future
  diagnostic test, and the lesson header tells the user about it.
- Topics are strict: only the user's selected topics, or all topics when
  none are selected. If nothing matches, the user is told so instead of
  being silently served other topics.
- Expressions already served to the user are never repeated as new.
- Ordering is deterministic (by id) and the size equals
  ``profile.new_items_per_day`` when that many expressions exist.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.db.models import Expression, Profile
from app.domain.profile import Level
from app.domain.srs import utc_now
from app.repositories import ExpressionProgressRepository, ExpressionRepository
from app.services.analytics import (
    EXPRESSION_COMPLETED,
    EXPRESSION_SHOWN,
    LESSON_COMPLETED,
    LESSON_STARTED,
    AnalyticsService,
    EventSource,
)

logger = logging.getLogger(__name__)

#: Level used when the profile level is unknown; see module docstring.
DEFAULT_UNKNOWN_LEVEL = Level.A2

UnavailableReason = Literal[
    "no_expressions_for_topics",
    "no_expressions_for_level",
    "all_served",
]


@dataclass(frozen=True)
class LessonPlan:
    """A freshly built lesson with its expressions."""

    expressions: list[Expression]
    used_default_level: bool = field(default=False)
    lesson_ref: str = field(default="")


@dataclass(frozen=True)
class LessonUnavailable:
    """Why no lesson could be built, with a user-facing hint."""

    reason: UnavailableReason


class LessonService:
    """Builds lessons from the dictionary, recording progress atomically."""

    def __init__(
        self,
        expression_repository: ExpressionRepository,
        progress_repository: ExpressionProgressRepository,
        analytics: AnalyticsService,
    ) -> None:
        self._expressions = expression_repository
        self._progress = progress_repository
        self._analytics = analytics

    async def build_lesson(
        self,
        user_id: int,
        profile: Profile,
        *,
        now: datetime | None = None,
        source: EventSource = "telegram",
    ) -> LessonPlan | LessonUnavailable:
        """Return a new lesson for the user, or the reason there is none.

        Progress rows for the chosen expressions are created in the same
        session/transaction, so a served expression never reappears as
        new — even if the user abandons the lesson midway. Each served
        card gets its first review scheduled at ``now + 1 day``.
        """
        answered_at = now or utc_now()
        used_default_level = profile.level == Level.UNKNOWN.value
        level = DEFAULT_UNKNOWN_LEVEL.value if used_default_level else profile.level
        topics = list(profile.topics)

        served = await self._progress.get_served_expression_ids(user_id)
        candidates = await self._expressions.get_active(
            language=profile.target_language,
            level=level,
            topics=topics,
            limit=profile.new_items_per_day,
            exclude_ids=served or None,
        )
        if candidates:
            lesson_ref = uuid.uuid4().hex[:12]
            await self._progress.bulk_create(
                user_id,
                [expression.id for expression in candidates],
                now=answered_at,
            )
            logger.info(
                "built lesson for user_id=%s: %s expressions, level=%s%s",
                user_id,
                len(candidates),
                level,
                " (default)" if used_default_level else "",
            )
            await self._analytics.track(
                user_id,
                LESSON_STARTED,
                source,
                {
                    "lesson_ref": lesson_ref,
                    "expression_count": len(candidates),
                    "level": level,
                    "used_default_level": used_default_level,
                },
                occurred_at=answered_at,
            )
            for expression in candidates:
                await self._analytics.track(
                    user_id,
                    EXPRESSION_SHOWN,
                    source,
                    {
                        "expression_id": expression.id,
                        "lesson_ref": lesson_ref,
                    },
                    occurred_at=answered_at,
                )
            return LessonPlan(
                expressions=candidates,
                used_default_level=used_default_level,
                lesson_ref=lesson_ref,
            )

        reason = await self._diagnose_shortage(profile.target_language, level, topics)
        logger.info("no lesson for user_id=%s: %s", user_id, reason.reason)
        return reason

    async def _diagnose_shortage(
        self, language: str, level: str, topics: list[str]
    ) -> LessonUnavailable:
        """Distinguish "nothing for topics" / "nothing at this level" / "all served"."""
        total_for_topics = await self._expressions.count_active(
            language=language, topics=topics
        )
        if total_for_topics == 0:
            return LessonUnavailable(reason="no_expressions_for_topics")
        at_level = await self._expressions.count_active(
            language=language, topics=topics, level=level
        )
        if at_level == 0:
            return LessonUnavailable(reason="no_expressions_for_level")
        return LessonUnavailable(reason="all_served")

    async def get_expressions(self, expression_ids: list[int]) -> list[Expression]:
        """Fetch dictionary entries by id (used for lesson navigation)."""
        return await self._expressions.get_many_by_ids(expression_ids)

    async def finish_lesson(
        self,
        user_id: int,
        expression_ids: list[int],
        *,
        source: EventSource = "telegram",
        lesson_ref: str | None = None,
        expressions_total: int | None = None,
        duration_seconds: float | None = None,
    ) -> int:
        """Mark the shown expressions as completed; return the count."""
        completed = await self._progress.mark_completed(user_id, expression_ids)
        logger.info(
            "finished lesson for user_id=%s: %s expressions completed",
            user_id,
            completed,
        )
        for expression_id in expression_ids[:completed]:
            await self._analytics.track(
                user_id,
                EXPRESSION_COMPLETED,
                source,
                {"expression_id": expression_id},
            )
        await self._analytics.track(
            user_id,
            LESSON_COMPLETED,
            source,
            {
                "lesson_ref": lesson_ref or "",
                "expressions_total": expressions_total or len(expression_ids),
                "expressions_shown": completed,
                "duration_seconds": (
                    round(duration_seconds) if duration_seconds is not None else None
                ),
            },
        )
        return completed
