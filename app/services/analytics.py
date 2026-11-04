"""Product analytics: one interface through which services emit events.

Events share the transaction of the business operation they describe
(the repository adds rows to the caller's session), so a rollback drops
both the change and its event. Properties are curated scalar values
(ids, counts, flags) built by services themselves — never raw user
input, messages, tokens or secrets.
"""

import logging
from datetime import datetime
from typing import Literal

from app.domain.srs import utc_now
from app.repositories import AnalyticsRepository

logger = logging.getLogger(__name__)

EventSource = Literal["telegram", "web", "system"]

PropertyValue = str | int | float | bool | None

#: Names used by services; kept in one place for greppability.
USER_REGISTERED = "user_registered"
PROFILE_COMPLETED = "profile_completed"
PROFILE_UPDATED = "profile_updated"
LESSON_STARTED = "lesson_started"
LESSON_COMPLETED = "lesson_completed"
EXPRESSION_SHOWN = "expression_shown"
EXPRESSION_COMPLETED = "expression_completed"
REVIEW_STARTED = "review_started"
REVIEW_ANSWERED = "review_answered"
REVIEW_COMPLETED = "review_completed"

#: Fields that must never appear in event properties.
FORBIDDEN_PROPERTY_KEYS = frozenset(
    {"token", "password", "text", "message", "translation", "example"}
)


class AnalyticsService:
    """Registers product events in the current transaction."""

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def track(
        self,
        user_id: int,
        event_name: str,
        source: EventSource,
        properties: dict[str, PropertyValue] | None = None,
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        """Queue one event; rolls back together with the business change."""
        safe_properties = properties or {}
        forbidden = FORBIDDEN_PROPERTY_KEYS.intersection(safe_properties)
        if forbidden:
            raise ValueError(
                f"sensitive properties are not allowed: {sorted(forbidden)}"
            )
        await self._repository.add(
            user_id,
            event_name,
            source,
            safe_properties,
            occurred_at=occurred_at or utc_now(),
        )
        logger.debug(
            "analytics event %s user_id=%s source=%s", event_name, user_id, source
        )
