"""Learning profile business logic."""

import logging
from typing import Any

from app.db.models import Profile
from app.repositories import ProfileRepository
from app.schemas.profile import ProfileCreate, ProfileUpdate
from app.services.analytics import (
    PROFILE_COMPLETED,
    PROFILE_UPDATED,
    AnalyticsService,
    EventSource,
)

logger = logging.getLogger(__name__)


class ProfileNotConfiguredError(Exception):
    """Raised when a profile operation needs a profile that does not exist."""


class ProfileService:
    """Reads, creates and partially updates learning profiles."""

    def __init__(
        self, repository: ProfileRepository, analytics: AnalyticsService
    ) -> None:
        self._repository = repository
        self._analytics = analytics

    async def get_profile(self, user_id: int) -> Profile | None:
        """Return the user's profile, or ``None`` if onboarding is not done."""
        return await self._repository.get_by_user_id(user_id)

    async def save_profile(
        self, user_id: int, data: ProfileCreate, *, source: EventSource = "telegram"
    ) -> Profile:
        """Create or fully replace the profile from onboarding.

        Re-running onboarding keeps the same row (id and created_at are
        preserved) — every confirmed field is overwritten.
        """
        existing = await self._repository.get_by_user_id(user_id)
        if existing is None:
            profile = await self._repository.create(data, user_id)
            logger.info("created profile for user_id=%s", user_id)
            await self._analytics.track(
                user_id, PROFILE_COMPLETED, source, {"level": data.level.value}
            )
            return profile
        full_patch = ProfileUpdate(**data.model_dump())
        profile = await self._repository.apply_update(existing, full_patch)
        logger.info("replaced profile for user_id=%s", user_id)
        await self._analytics.track(
            user_id,
            PROFILE_UPDATED,
            source,
            {"fields": "full"},
        )
        return profile

    async def update_profile(
        self,
        user_id: int,
        update: ProfileUpdate,
        *,
        source: EventSource = "telegram",
    ) -> Profile:
        """Apply a partial patch without touching other fields.

        The merged result (stored values plus the patch) is re-validated
        as a complete profile, so cross-field rules still hold after a
        single-setting edit.
        """
        profile = await self._repository.get_by_user_id(user_id)
        if profile is None:
            raise ProfileNotConfiguredError(
                f"profile for user_id={user_id} does not exist"
            )
        merged: dict[str, Any] = {
            "native_language": profile.native_language,
            "target_language": profile.target_language,
            "level": profile.level,
            "learning_goal": profile.learning_goal,
            "topics": list(profile.topics),
            "preferred_accent": profile.preferred_accent,
            "daily_study_minutes": profile.daily_study_minutes,
            "new_items_per_day": profile.new_items_per_day,
            "lesson_time": profile.lesson_time,
            "review_time": profile.review_time,
            "timezone": profile.timezone,
        }
        changed_fields = sorted(update.model_dump(exclude_unset=True))
        merged.update(update.model_dump(exclude_unset=True))
        ProfileCreate.model_validate(merged)
        updated = await self._repository.apply_update(profile, update)
        logger.info(
            "updated profile fields %s for user_id=%s",
            changed_fields,
            user_id,
        )
        await self._analytics.track(
            user_id,
            PROFILE_UPDATED,
            source,
            {"fields": ",".join(changed_fields)},
        )
        return updated

    def build_draft(self, profile: Profile) -> dict[str, Any]:
        """Convert a stored profile into FSM prefill data for onboarding."""
        return {
            "native_language": profile.native_language,
            "target_language": profile.target_language,
            "level": profile.level,
            "learning_goal": profile.learning_goal,
            "topics": list(profile.topics),
            "preferred_accent": profile.preferred_accent,
            "daily_study_minutes": profile.daily_study_minutes,
            "new_items_per_day": profile.new_items_per_day,
            "lesson_time": profile.lesson_time.strftime("%H:%M"),
            "review_time": profile.review_time.strftime("%H:%M"),
            "timezone": profile.timezone,
        }
