"""Tests for the review service (repositories mocked)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from app.db.models import Expression, ExpressionProgress
from app.domain.srs import SrsUpdate
from app.repositories import (
    ExpressionProgressRepository,
    ExpressionRepository,
)
from app.services.reviews import (
    REVIEW_BATCH_SIZE,
    ReviewPlan,
    ReviewService,
    ReviewUnavailable,
)

NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)


def make_expression(expression_id: int) -> Expression:
    return Expression(
        id=expression_id,
        language="en",
        text=f"expression {expression_id}",
        translation="перевод",
        ipa="aɪ piː eɪ",
        phonetic_hint_ru="пОдсказка",
        example="example",
        example_translation="пример",
        level="A1",
        topic="everyday",
    )


def make_progress(expression_id: int) -> ExpressionProgress:
    return ExpressionProgress(
        id=expression_id * 10,
        user_id=11,
        expression_id=expression_id,
        next_review_at=NOW,
    )


def make_service(
    due: list[ExpressionProgress] | None = None,
    by_pair: ExpressionProgress | None = None,
) -> tuple[ReviewService, AsyncMock, AsyncMock]:
    progress_repository = AsyncMock(spec=ExpressionProgressRepository)
    progress_repository.get_due.return_value = due or []
    progress_repository.get_by_pair.return_value = by_pair
    expression_repository = AsyncMock(spec=ExpressionRepository)
    expression_repository.get_many_by_ids.return_value = [
        make_expression(row.expression_id) for row in due or []
    ]
    analytics = AsyncMock()
    service = ReviewService(progress_repository, expression_repository, analytics)
    return service, progress_repository, expression_repository


async def test_build_review_pairs_due_rows_with_expressions() -> None:
    due = [make_progress(3), make_progress(7)]
    service, progress_repository, _ = make_service(due=due)

    plan = await service.build_review(11, now=NOW)

    progress_repository.get_due.assert_awaited_once_with(
        11, now=NOW, limit=REVIEW_BATCH_SIZE
    )
    assert isinstance(plan, ReviewPlan)
    assert [row.expression_id for row in plan.progress_rows] == [3, 7]
    assert [expression.id for expression in plan.expressions] == [3, 7]


async def test_build_review_without_due_cards() -> None:
    service, _, _ = make_service(due=[])

    result = await service.build_review(11, now=NOW)

    assert isinstance(result, ReviewUnavailable)
    assert result.reason == "no_due_reviews"


async def test_build_review_drops_missing_expressions() -> None:
    """Due rows referencing deleted expressions never crash the session."""
    due = [make_progress(3)]
    service, _, expression_repository = make_service(due=due)
    expression_repository.get_many_by_ids.return_value = []

    result = await service.build_review(11, now=NOW)

    assert isinstance(result, ReviewUnavailable)


async def test_apply_answer_persists_srs_update() -> None:
    progress = make_progress(5)
    progress.ease_factor = 2.5
    progress.interval_days = 6
    progress.review_count = 2
    service, progress_repository, _ = make_service(by_pair=progress)

    update = await service.apply_answer(11, 5, remembered=True, now=NOW)

    progress_repository.update_srs.assert_awaited_once()
    persisted: SrsUpdate = progress_repository.update_srs.await_args.args[1]
    assert persisted.review_count == 3
    assert persisted.interval_days == 15
    assert persisted.next_review_at == NOW + timedelta(days=15)
    assert update is not None and update.review_count == 3


async def test_apply_answer_for_missing_progress_returns_none() -> None:
    """A stale session (no progress row) must not create anything."""
    service, progress_repository, _ = make_service(by_pair=None)

    update = await service.apply_answer(11, 999, remembered=True, now=NOW)

    assert update is None
    progress_repository.update_srs.assert_not_awaited()


def test_review_batch_size_is_bounded() -> None:
    assert isinstance(REVIEW_BATCH_SIZE, int)
    assert 0 < REVIEW_BATCH_SIZE <= 100


async def test_get_expressions_delegates_to_repository() -> None:
    service, _, expression_repository = make_service()

    await service.get_expressions([1, 2])

    expression_repository.get_many_by_ids.assert_awaited_once_with([1, 2])
