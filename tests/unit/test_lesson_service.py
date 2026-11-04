"""Tests for lesson selection logic (repositories mocked)."""

from typing import Any
from unittest.mock import ANY, AsyncMock

from app.db.models import Expression, Profile
from app.domain.profile import Level
from app.repositories import (
    ExpressionProgressRepository,
    ExpressionRepository,
)
from app.services.analytics import AnalyticsService  # noqa: F401
from app.services.lessons import (
    DEFAULT_UNKNOWN_LEVEL,
    LessonPlan,
    LessonService,
    LessonUnavailable,
)


def make_profile(**overrides: Any) -> Profile:
    from datetime import date, time

    values: dict[str, Any] = {
        "id": 1,
        "user_id": 11,
        "native_language": "ru",
        "target_language": "en",
        "level": "B1",
        "learning_goal": "work",
        "topics": ["work"],
        "preferred_accent": "uk",
        "daily_study_minutes": 15,
        "new_items_per_day": 5,
        "lesson_time": time(8, 0),
        "review_time": time(19, 0),
        "timezone": "Europe/Moscow",
        "created_at": date(2026, 11, 3),
        "updated_at": date(2026, 11, 3),
    }
    values.update(overrides)
    return Profile(**values)


def make_expression(expression_id: int, level: str = "B1") -> Expression:
    return Expression(
        id=expression_id,
        language="en",
        text=f"expression {expression_id}",
        translation=f"перевод {expression_id}",
        ipa="aɪ piː eɪ",
        phonetic_hint_ru="пОдсказка",
        example="example",
        example_translation="пример",
        level=level,
        topic="work",
    )


def make_service(
    active: list[Expression] | None = None,
    served: set[int] | None = None,
    counts: list[int] | None = None,
) -> tuple[LessonService, AsyncMock, AsyncMock]:
    expression_repository = AsyncMock(spec=ExpressionRepository)
    expression_repository.get_active.return_value = active or []
    expression_repository.count_active.side_effect = counts or [0, 0]
    progress_repository = AsyncMock(spec=ExpressionProgressRepository)
    progress_repository.get_served_expression_ids.return_value = served or set()
    analytics = AsyncMock()
    service = LessonService(expression_repository, progress_repository, analytics)
    return service, expression_repository, progress_repository


async def test_builds_lesson_with_profile_filters() -> None:
    expressions = [make_expression(1), make_expression(2)]
    service, expression_repository, progress_repository = make_service(
        active=expressions
    )
    profile = make_profile(level="B1", topics=["work"], new_items_per_day=5)

    plan = await service.build_lesson(11, profile)

    expression_repository.get_active.assert_awaited_once_with(
        language="en", level="B1", topics=["work"], limit=5, exclude_ids=None
    )
    progress_repository.bulk_create.assert_awaited_once_with(11, [1, 2], now=ANY)
    assert isinstance(plan, LessonPlan)
    assert [expression.id for expression in plan.expressions] == [1, 2]
    assert plan.used_default_level is False


async def test_served_expressions_are_excluded_from_new_lessons() -> None:
    service, expression_repository, _ = make_service(active=[], served={7, 8})
    profile = make_profile()

    await service.build_lesson(11, profile)

    call_kwargs = expression_repository.get_active.await_args.kwargs
    assert call_kwargs["exclude_ids"] == {7, 8}


async def test_unknown_level_uses_explicit_default() -> None:
    """Level 'unknown' maps to A2 and the lesson says so."""
    expressions = [make_expression(1, level="A2")]
    service, expression_repository, _ = make_service(active=expressions)
    profile = make_profile(level="unknown")

    plan = await service.build_lesson(11, profile)

    assert expression_repository.get_active.await_args.kwargs["level"] == (
        DEFAULT_UNKNOWN_LEVEL.value
    )
    assert isinstance(plan, LessonPlan)
    assert plan.used_default_level is True


async def test_empty_topics_mean_any_topic() -> None:
    service, expression_repository, _ = make_service(active=[])
    profile = make_profile(topics=[])

    await service.build_lesson(11, profile)

    assert expression_repository.get_active.await_args.kwargs["topics"] == []


async def test_reports_missing_topic_expressions() -> None:
    """Nothing in the dictionary for the selected topics at all."""
    service, expression_repository, _ = make_service(active=[])
    expression_repository.count_active.side_effect = [0]
    profile = make_profile(topics=["music"])

    result = await service.build_lesson(11, profile)

    assert isinstance(result, LessonUnavailable)
    assert result.reason == "no_expressions_for_topics"


async def test_reports_level_mismatch() -> None:
    """Expressions exist for the topics, but not at the user's level."""
    service, expression_repository, _ = make_service(active=[])
    expression_repository.count_active.side_effect = [5, 0]
    profile = make_profile(level="C2", topics=["work"])

    result = await service.build_lesson(11, profile)

    assert isinstance(result, LessonUnavailable)
    assert result.reason == "no_expressions_for_level"


async def test_reports_all_served() -> None:
    """Everything matching the profile has already been served."""
    service, expression_repository, _ = make_service(active=[])
    expression_repository.count_active.side_effect = [3, 3]
    profile = make_profile()

    result = await service.build_lesson(11, profile)

    assert isinstance(result, LessonUnavailable)
    assert result.reason == "all_served"


async def test_finish_lesson_marks_progress() -> None:
    service, _, progress_repository = make_service()
    progress_repository.mark_completed.return_value = 2

    completed = await service.finish_lesson(11, [1, 2])

    progress_repository.mark_completed.assert_awaited_once_with(11, [1, 2])
    assert completed == 2


def test_default_unknown_level_is_a2() -> None:
    """The fallback level must stay a conscious, documented choice."""
    assert DEFAULT_UNKNOWN_LEVEL is Level.A2
