"""User learning statistics.

All numbers come from durable sources: ``expression_progress`` for
cards and reviews, ``analytics_events`` for lesson completions (counted
from the moment event tracking was introduced). The daily streak is
computed from activity timestamps converted to the user's timezone.
Both Telegram (/stats) and the future Web dashboard consume this
service; no presentation logic lives here.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalyticsEvent, ExpressionProgress, Profile
from app.domain.srs import utc_now
from app.repositories import AnalyticsRepository
from app.services.analytics import LESSON_COMPLETED, REVIEW_ANSWERED

#: How far back the daily activity chart reaches.
PROGRESS_DAYS = 30


@dataclass(frozen=True)
class StatsOverview:
    """Headline numbers of one user."""

    expressions_shown: int
    expressions_completed: int
    lessons_completed: int
    reviews_total: int
    remember_ratio: float | None
    due_count: int
    streak_days: int


@dataclass(frozen=True)
class DayActivity:
    """One day of the activity chart."""

    day: date
    expressions_shown: int
    reviews: int


class StatsService:
    """Computes learning statistics for one user at a time."""

    def __init__(
        self,
        session: AsyncSession,
        analytics_repository: AnalyticsRepository,
    ) -> None:
        self._session = session
        self._analytics = analytics_repository

    async def get_overview(
        self,
        user_id: int,
        *,
        now: datetime | None = None,
    ) -> StatsOverview:
        """Headline stats; isolation by user_id is enforced by queries."""
        current = now or utc_now()
        shown = await self._count(
            select(func.count())
            .select_from(ExpressionProgress)
            .where(ExpressionProgress.user_id == user_id)
        )
        completed = await self._count(
            select(func.count())
            .select_from(ExpressionProgress)
            .where(
                ExpressionProgress.user_id == user_id,
                ExpressionProgress.completed_at.is_not(None),
            )
        )
        due = await self._count(
            select(func.count())
            .select_from(ExpressionProgress)
            .where(
                ExpressionProgress.user_id == user_id,
                ExpressionProgress.next_review_at.is_not(None),
                ExpressionProgress.next_review_at <= current,
            )
        )
        lessons = await self._analytics.count_for_user(
            user_id, event_name=LESSON_COMPLETED
        )
        reviews_total = await self._analytics.count_for_user(
            user_id, event_name=REVIEW_ANSWERED
        )
        remembered = await self._analytics.count_for_user_with_filter(
            user_id,
            event_name=REVIEW_ANSWERED,
            properties_key="answer",
            properties_value="remember",
        )
        remember_ratio = remembered / reviews_total if reviews_total > 0 else None
        return StatsOverview(
            expressions_shown=shown,
            expressions_completed=completed,
            lessons_completed=lessons,
            reviews_total=reviews_total,
            remember_ratio=remember_ratio,
            due_count=due,
            streak_days=await self._streak_days(user_id, current),
        )

    async def get_progress(
        self,
        user_id: int,
        *,
        days: int = PROGRESS_DAYS,
        now: datetime | None = None,
    ) -> list[DayActivity]:
        """Daily shown/review counts for the last ``days`` days.

        Uses expression_progress history (available since the user's
        first lesson) plus review events for the answer counts.
        """
        current = now or utc_now()
        rows = (
            await self._session.execute(
                select(
                    ExpressionProgress.shown_at,
                ).where(
                    ExpressionProgress.user_id == user_id,
                    ExpressionProgress.shown_at >= current - timedelta(days=days),
                )
            )
        ).all()
        review_rows = (
            await self._session.execute(
                select(AnalyticsEvent.occurred_at).where(
                    AnalyticsEvent.user_id == user_id,
                    AnalyticsEvent.event_name == REVIEW_ANSWERED,
                    AnalyticsEvent.occurred_at >= current - timedelta(days=days),
                )
            )
        ).all()
        shown_per_day: dict[date, int] = {}
        for (shown_at,) in rows:
            shown_per_day[shown_at.date()] = shown_per_day.get(shown_at.date(), 0) + 1
        reviews_per_day: dict[date, int] = {}
        for (occurred_at,) in review_rows:
            reviews_per_day[occurred_at.date()] = (
                reviews_per_day.get(occurred_at.date(), 0) + 1
            )
        today = current.date()
        return [
            DayActivity(
                day=day,
                expressions_shown=shown_per_day.get(day, 0),
                reviews=reviews_per_day.get(day, 0),
            )
            for day in sorted({**shown_per_day, **reviews_per_day}.keys())
            if (today - day).days < days
        ]

    async def _streak_days(self, user_id: int, current: datetime) -> int:
        """Consecutive days (in the profile timezone) with any activity.

        Activity sources: cards shown and cards reviewed. If the user
        has no profile timezone, UTC days are used.
        """
        zone = await self._profile_zone(user_id)
        latest_rows = (
            await self._session.execute(
                select(
                    ExpressionProgress.shown_at,
                    ExpressionProgress.last_reviewed_at,
                )
                .where(ExpressionProgress.user_id == user_id)
                .order_by(ExpressionProgress.id.desc())
                .limit(2000)
            )
        ).all()
        if not latest_rows:
            return 0
        activity_days: set[date] = set()
        for shown_at, last_reviewed_at in latest_rows:
            activity_days.add(self._local_date(shown_at, zone))
            if last_reviewed_at is not None:
                activity_days.add(self._local_date(last_reviewed_at, zone))
        streak = 0
        day = self._local_date(current, zone)
        if day not in activity_days:
            # A streak survives until the end of the local day: allow
            # counting from yesterday only if today has no activity yet
            # and yesterday does.
            day = day - timedelta(days=1)
        while day in activity_days:
            streak += 1
            day = day - timedelta(days=1)
        return streak

    async def _profile_zone(self, user_id: int) -> ZoneInfo | None:
        row = (
            await self._session.execute(
                select(Profile.timezone).where(Profile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        try:
            return ZoneInfo(row)
        except (ValueError, ZoneInfoNotFoundError):
            return None

    @staticmethod
    def _local_date(moment: datetime, zone: ZoneInfo | None) -> date:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo("UTC"))
        if zone is not None:
            moment = moment.astimezone(zone)
        return moment.date()

    async def _count(self, statement: Any) -> int:
        result = await self._session.execute(statement)
        return int(result.scalar_one())
