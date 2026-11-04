"""Public HTTP API v1 for non-Telegram clients.

Every endpoint requires a personal bearer token and operates only on
the authenticated user's data; ownership is enforced inside services by
user_id-scoped queries. Business logic is shared with Telegram handlers
one-to-one — no duplication.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import (
    CurrentUserId,
    LessonDep,
    ProfileDep,
    ReviewDep,
    StatsDep,
)
from app.db.models import Expression, Profile
from app.schemas.api import (
    DueReviewsOut,
    LessonCardOut,
    LessonOut,
    ProfileOut,
    ReviewAnswerIn,
    ReviewAnswerOut,
    StatsOverviewOut,
    StatsProgressOut,
)
from app.schemas.profile import ProfileUpdate
from app.services import ProfileNotConfiguredError
from app.services.lessons import LessonPlan
from app.services.reviews import ReviewUnavailable

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


def _card_out(expression: Expression) -> LessonCardOut:
    return LessonCardOut(
        expression_id=expression.id,
        text=expression.text,
        translation=expression.translation,
        ipa=expression.ipa,
        phonetic_hint_ru=expression.phonetic_hint_ru,
        example=expression.example,
        example_translation=expression.example_translation,
        level=expression.level,
        topic=expression.topic,
    )


async def _require_profile(user_id: int, profiles: ProfileDep) -> Profile:
    profile = await profiles.get_profile(user_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )
    return profile


@router.get("/profile", response_model=ProfileOut)
async def get_profile(user_id: CurrentUserId, profiles: ProfileDep) -> Profile:
    """Return the user's profile."""
    return await _require_profile(user_id, profiles)


@router.patch("/profile", response_model=ProfileOut)
async def patch_profile(
    user_id: CurrentUserId,
    profiles: ProfileDep,
    update: ProfileUpdate,
) -> Profile:
    """Change individual profile settings."""
    await _require_profile(user_id, profiles)
    try:
        return await profiles.update_profile(user_id, update, source="web")
    except ProfileNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        ) from None


@router.post("/lessons", response_model=LessonOut, status_code=status.HTTP_201_CREATED)
async def create_lesson(
    user_id: CurrentUserId, profiles: ProfileDep, lessons: LessonDep
) -> LessonOut:
    """Build a new lesson from the user's profile.

    Returns cards, or the same shape with ``unavailable_reason`` set when
    nothing new matches the profile (topics, level or exhausted
    dictionary).
    """
    profile = await _require_profile(user_id, profiles)
    plan = await lessons.build_lesson(user_id, profile, source="web")
    if isinstance(plan, LessonPlan):
        return LessonOut(
            lesson_ref=plan.lesson_ref,
            used_default_level=plan.used_default_level,
            expressions=[_card_out(expression) for expression in plan.expressions],
        )
    return LessonOut(unavailable_reason=plan.reason)


@router.get("/reviews/due", response_model=DueReviewsOut)
async def due_reviews(user_id: CurrentUserId, reviews: ReviewDep) -> DueReviewsOut:
    """Cards whose review is due, oldest first (bounded by the batch size)."""
    plan = await reviews.build_review(user_id, source="web")
    if isinstance(plan, ReviewUnavailable):
        return DueReviewsOut(cards=[])
    return DueReviewsOut(
        cards=[_card_out(expression) for expression in plan.expressions]
    )


@router.post("/reviews/{expression_id}/answer", response_model=ReviewAnswerOut)
async def answer_review(
    user_id: CurrentUserId,
    expression_id: int,
    answer: ReviewAnswerIn,
    reviews: ReviewDep,
) -> ReviewAnswerOut:
    """Apply one spaced-repetition answer.

    Each call is one answer: clients send it exactly once per card, the
    same way Telegram answers once per button press.
    """
    update = await reviews.apply_answer(
        user_id, expression_id, remembered=answer.remembered, source="web"
    )
    if update is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Expression is not in your progress",
        )
    return ReviewAnswerOut(
        expression_id=expression_id,
        review_count=update.review_count,
        interval_days=update.interval_days,
        ease_factor=update.ease_factor,
        next_review_at=update.next_review_at,
    )


@router.get("/stats/overview", response_model=StatsOverviewOut)
async def stats_overview(user_id: CurrentUserId, stats: StatsDep) -> StatsOverviewOut:
    """Headline learning statistics of the authenticated user."""
    overview = await stats.get_overview(user_id)
    return StatsOverviewOut(
        expressions_shown=overview.expressions_shown,
        expressions_completed=overview.expressions_completed,
        lessons_completed=overview.lessons_completed,
        reviews_total=overview.reviews_total,
        remember_ratio=overview.remember_ratio,
        due_count=overview.due_count,
        streak_days=overview.streak_days,
    )


@router.get("/stats/progress", response_model=StatsProgressOut)
async def stats_progress(user_id: CurrentUserId, stats: StatsDep) -> StatsProgressOut:
    """Daily activity for the last 30 days."""
    progress = await stats.get_progress(user_id)
    return StatsProgressOut(
        days=[
            {
                "day": day.day,
                "expressions_shown": day.expressions_shown,
                "reviews": day.reviews,
            }
            for day in progress
        ]
    )
