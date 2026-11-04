"""Review sessions: serve due cards and apply SRS answers.

The scheduling math lives in :mod:`app.domain.srs`; this service loads
due progress rows, pairs them with dictionary expressions and persists
the result of each answer. Nothing here touches Telegram.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.db.models import Expression, ExpressionProgress
from app.domain.srs import SrsState, SrsUpdate, apply_answer, utc_now
from app.repositories import ExpressionProgressRepository, ExpressionRepository
from app.services.analytics import (
    REVIEW_ANSWERED,
    REVIEW_COMPLETED,
    REVIEW_STARTED,
    AnalyticsService,
    EventSource,
)

logger = logging.getLogger(__name__)

#: Upper bound of one review session, so a long backlog cannot produce a
#: giant message thread in one go.
REVIEW_BATCH_SIZE = 20

UnavailableReason = Literal["no_due_reviews"]


@dataclass(frozen=True)
class ReviewPlan:
    """A built review session: due progress rows and their expressions."""

    progress_rows: list[ExpressionProgress]
    expressions: list[Expression]


@dataclass(frozen=True)
class ReviewUnavailable:
    """Why no review session could be built."""

    reason: UnavailableReason


class ReviewService:
    """Builds review sessions and applies spaced-repetition answers."""

    def __init__(
        self,
        progress_repository: ExpressionProgressRepository,
        expression_repository: ExpressionRepository,
        analytics: AnalyticsService,
    ) -> None:
        self._progress = progress_repository
        self._expressions = expression_repository
        self._analytics = analytics

    async def build_review(
        self,
        user_id: int,
        *,
        now: datetime | None = None,
        source: EventSource = "telegram",
    ) -> ReviewPlan | ReviewUnavailable:
        """Return the user's due cards, or the reason there are none."""
        answered_at = now or utc_now()
        due_rows = await self._progress.get_due(
            user_id, now=answered_at, limit=REVIEW_BATCH_SIZE
        )
        if not due_rows:
            return ReviewUnavailable(reason="no_due_reviews")
        expressions = await self._expressions.get_many_by_ids(
            [row.expression_id for row in due_rows]
        )
        expressions_by_id = {expression.id: expression for expression in expressions}
        cards = [
            (row, expressions_by_id[row.expression_id])
            for row in due_rows
            if row.expression_id in expressions_by_id
        ]
        if not cards:
            # Due rows without expressions (dictionary changed) — drop
            # them from the session instead of crashing it.
            logger.warning(
                "review for user_id=%s: due rows reference missing " "expressions",
                user_id,
            )
            return ReviewUnavailable(reason="no_due_reviews")
        logger.info(
            "built review for user_id=%s: %s cards",
            user_id,
            len(cards),
        )
        await self._analytics.track(
            user_id,
            REVIEW_STARTED,
            source,
            {"card_count": len(cards)},
            occurred_at=answered_at,
        )
        return ReviewPlan(
            progress_rows=[row for row, _ in cards],
            expressions=[expression for _, expression in cards],
        )

    async def get_expressions(self, expression_ids: list[int]) -> list[Expression]:
        """Fetch dictionary entries by id (used for session navigation)."""
        return await self._expressions.get_many_by_ids(expression_ids)

    async def apply_answer(
        self,
        user_id: int,
        expression_id: int,
        *,
        remembered: bool,
        now: datetime | None = None,
        source: EventSource = "telegram",
    ) -> SrsUpdate | None:
        """Apply one answer to the card's progress row.

        Returns the new scheduling state, or ``None`` when the user has
        no progress row for the expression (stale session).
        """
        answered_at = now or utc_now()
        progress = await self._progress.get_by_pair(user_id, expression_id)
        if progress is None:
            return None
        interval_before = progress.interval_days
        update = apply_answer(
            SrsState(
                ease_factor=progress.ease_factor,
                interval_days=progress.interval_days,
                review_count=progress.review_count,
            ),
            remembered=remembered,
            now=answered_at,
        )
        await self._progress.update_srs(progress, update)
        logger.info(
            "review answer user_id=%s expression_id=%s remembered=%s "
            "next_review_at=%s",
            user_id,
            expression_id,
            remembered,
            update.next_review_at,
        )
        await self._analytics.track(
            user_id,
            REVIEW_ANSWERED,
            source,
            {
                "expression_id": expression_id,
                "answer": "remember" if remembered else "forgot",
                "interval_before": interval_before,
                "interval_after": update.interval_days,
            },
            occurred_at=answered_at,
        )
        return update

    async def complete_session(
        self,
        user_id: int,
        *,
        remembered: int,
        forgot: int,
        source: EventSource = "telegram",
    ) -> None:
        """Record the summary event of a finished review session."""
        await self._analytics.track(
            user_id,
            REVIEW_COMPLETED,
            source,
            {"remembered": remembered, "forgot": forgot},
        )
