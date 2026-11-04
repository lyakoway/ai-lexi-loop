"""Repository for the ``profiles`` table."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Profile
from app.repositories.errors import wrap_storage_errors
from app.schemas.profile import ProfileCreate, ProfileUpdate


class ProfileRepository:
    """Data access for :class:`~app.db.models.Profile`.

    Like the user repository, it never commits: transaction boundaries
    belong to ``session_scope``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: int) -> Profile | None:
        """Return the profile owned by the given user, or ``None``."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(Profile).where(Profile.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def create(self, data: ProfileCreate, user_id: int) -> Profile:
        """Insert a new profile for the user and refresh server defaults."""
        async with wrap_storage_errors():
            profile = Profile(
                user_id=user_id,
                native_language=data.native_language,
                target_language=data.target_language,
                level=data.level.value,
                learning_goal=data.learning_goal.value,
                topics=[topic.value for topic in data.topics],
                preferred_accent=data.preferred_accent.value,
                daily_study_minutes=data.daily_study_minutes,
                new_items_per_day=data.new_items_per_day,
                lesson_time=data.lesson_time,
                review_time=data.review_time,
                timezone=data.timezone,
            )
            self._session.add(profile)
            await self._session.flush()
            await self._session.refresh(profile)
            return profile

    async def apply_update(self, profile: Profile, update: ProfileUpdate) -> Profile:
        """Write only the fields present in the patch onto the profile.

        Enum values are StrEnum members, i.e. plain ``str`` subclasses,
        so they bind to VARCHAR columns as-is.
        """
        async with wrap_storage_errors():
            changes = update.model_dump(exclude_unset=True)
            for field, value in changes.items():
                setattr(profile, field, value)
            await self._session.flush()
            await self._session.refresh(profile)
            return profile
