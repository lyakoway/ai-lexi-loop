"""Repository for analytics events."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalyticsEvent
from app.repositories.errors import wrap_storage_errors


class AnalyticsRepository:
    """Data access for product events.

    Events are inserted through the owning service's session, so they
    share the business transaction (commit or rollback together).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        user_id: int,
        event_name: str,
        source: str,
        properties: dict[str, str | int | float | bool | None],
        *,
        occurred_at: datetime,
    ) -> AnalyticsEvent:
        """Queue one event in the current transaction."""
        event = AnalyticsEvent(
            user_id=user_id,
            event_name=event_name,
            source=source,
            properties=properties,
            occurred_at=occurred_at,
        )
        self._session.add(event)
        return event

    async def count_for_user(self, user_id: int, *, event_name: str) -> int:
        """How many events of a name the user has."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(func.count())
                .select_from(AnalyticsEvent)
                .where(
                    AnalyticsEvent.user_id == user_id,
                    AnalyticsEvent.event_name == event_name,
                )
            )
            return int(result.scalar_one())

    async def count_for_user_with_filter(
        self,
        user_id: int,
        *,
        event_name: str,
        properties_key: str,
        properties_value: str,
    ) -> int:
        """Count events whose JSONB property equals a value."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(func.count())
                .select_from(AnalyticsEvent)
                .where(
                    AnalyticsEvent.user_id == user_id,
                    AnalyticsEvent.event_name == event_name,
                    AnalyticsEvent.properties[properties_key].astext
                    == properties_value,
                )
            )
            return int(result.scalar_one())
