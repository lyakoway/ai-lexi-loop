"""Integration tests for quiz grading on a real PostgreSQL.

Covers the DoD matrix: meaning-correct answers pass, real errors are
classified (grammar/vocabulary/usage), invalid model output falls back
without breaking the quiz or touching the schedule, and every graded
attempt writes a ``quiz_evaluations`` telemetry row — metrics only,
never the answer text.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import Expression, ExpressionProgress, Profile, QuizEvaluation
from app.db.session import SessionFactory, session_scope
from app.services.quiz import (
    QuizGradeResult,
    QuizQuestion,
    QuizService,
    QuizUnavailable,
)
from tests.conftest import FakeLlmClient, make_settings
from tests.integration.conftest import create_profile_row, reset_database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)

CORRECT_JSON = (
    '{"is_correct": true, "error_type": "none", '
    '"explanation": "Смысл передан верно.", "corrected": ""}'
)
GRAMMAR_JSON = (
    '{"is_correct": false, "error_type": "grammar", '
    '"explanation": "Идиома: break a leg, без артикля.", '
    '"corrected": "Break a leg"}'
)
VOCABULARY_JSON = (
    '{"is_correct": false, "error_type": "vocabulary", '
    '"explanation": "Нужен другой глагол.", "corrected": "Break a leg"}'
)
USAGE_JSON = (
    '{"is_correct": false, "error_type": "usage", '
    '"explanation": "Так не говорят.", "corrected": "Break a leg"}'
)
# Valid JSON that violates the schema's cross-field rules (correct=True
# with a real error class) — parse_structured raises LlmOutputFormatError.
SCHEMA_INVALID_JSON = '{"is_correct": true, "error_type": "grammar"}'


async def prepare_user(
    session_factory: SessionFactory, telegram_id: int, *, fresh: bool = True
) -> int:
    async with session_scope(session_factory) as session:
        if fresh:
            await reset_database(session)
        profile = await create_profile_row(session, telegram_id=telegram_id)
        return profile.user_id


async def seed_served_expression(
    session_factory: SessionFactory,
    user_id: int,
    text: str,
    *,
    translation: str = "перевод",
) -> int:
    """Insert an expression and serve it to the user (progress row)."""
    async with session_scope(session_factory) as session:
        expression = Expression(
            language="ts",
            text=text,
            translation=translation,
            ipa="aɪ piː eɪ",
            phonetic_hint_ru="пОдсказка",
            example="example",
            example_translation="пример",
            level="A1",
            topic="everyday",
        )
        session.add(expression)
        await session.flush()
        session.add(
            ExpressionProgress(
                user_id=user_id,
                expression_id=expression.id,
                next_review_at=NOW + timedelta(days=1),
            )
        )
        await session.flush()
        return expression.id


async def load_progress(
    session_factory: SessionFactory, user_id: int, expression_id: int
) -> ExpressionProgress:
    async with session_scope(session_factory) as session:
        result = await session.execute(
            select(ExpressionProgress).where(
                ExpressionProgress.user_id == user_id,
                ExpressionProgress.expression_id == expression_id,
            )
        )
        return result.scalar_one()


async def load_evaluations(
    session_factory: SessionFactory, user_id: int
) -> list[QuizEvaluation]:
    async with session_scope(session_factory) as session:
        result = await session.execute(
            select(QuizEvaluation)
            .where(QuizEvaluation.user_id == user_id)
            .order_by(QuizEvaluation.id)
        )
        return list(result.scalars().all())


def make_service(
    session_factory: SessionFactory, llm: Any, **settings_overrides: Any
) -> QuizService:
    settings = make_settings(database_url="postgresql://u:p@h/db", **settings_overrides)
    return QuizService(session_factory, llm, settings)


# ---------------------------------------------------------------------------
# LLM verdicts
# ---------------------------------------------------------------------------


async def test_meaning_correct_answer_passes(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Correct by meaning, different by form → is_correct=True."""
    user_id = await prepare_user(session_factory, 9700)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg", translation="удачи"
    )
    service = make_service(session_factory, FakeLlmClient([CORRECT_JSON]))

    result = await service.grade_answer(
        user_id, expression_id, "break your leg", now=NOW
    )

    assert isinstance(result, QuizGradeResult)
    assert result.is_correct is True
    assert result.error_type == "none"
    assert result.streak == 1
    progress = await load_progress(session_factory, user_id, expression_id)
    assert progress.review_count == 1
    assert progress.interval_days == 1  # first success: one day


async def test_grammar_error_is_classified_and_resets_schedule(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9701)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([GRAMMAR_JSON]))

    result = await service.grade_answer(user_id, expression_id, "I break leg", now=NOW)

    assert isinstance(result, QuizGradeResult)
    assert result.is_correct is False
    assert result.error_type == "grammar"
    assert result.explanation
    assert result.corrected == "Break a leg"
    assert result.streak == 0
    progress = await load_progress(session_factory, user_id, expression_id)
    assert progress.review_count == 0  # reset
    assert progress.interval_days == 1
    assert progress.lapse_count == 1
    assert progress.next_review_at == NOW + timedelta(days=1)


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        (VOCABULARY_JSON, "vocabulary"),
        (USAGE_JSON, "usage"),
    ],
)
async def test_error_types_pass_through(
    migrated_db: None,
    session_factory: SessionFactory,
    payload: str,
    error_type: str,
) -> None:
    user_id = await prepare_user(session_factory, 9702)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([payload]))

    result = await service.grade_answer(user_id, expression_id, "wrong", now=NOW)

    assert isinstance(result, QuizGradeResult)
    assert result.error_type == error_type
    assert result.is_correct is False


# ---------------------------------------------------------------------------
# Scheduler: streaks and determinism (SM-2 through quiz answers)
# ---------------------------------------------------------------------------


async def test_success_streak_grows_interval_deterministically(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Three correct answers: 1 → 6 → 15 days, from fixed dates only."""
    user_id = await prepare_user(session_factory, 9703)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([CORRECT_JSON] * 3))

    for step in range(3):
        moment = NOW + timedelta(days=step)
        result = await service.grade_answer(
            user_id, expression_id, "same answer", now=moment
        )
        assert isinstance(result, QuizGradeResult)
        assert result.srs is not None
        assert result.srs.interval_days == (1, 6, 15)[step]
        assert result.srs.next_review_at == moment + timedelta(
            days=result.srs.interval_days
        )

    progress = await load_progress(session_factory, user_id, expression_id)
    assert progress.review_count == 3
    assert progress.interval_days == 15
    assert progress.lapse_count == 0


async def test_failure_after_streak_resets_interval(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9704)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([CORRECT_JSON, GRAMMAR_JSON]))

    first = await service.grade_answer(user_id, expression_id, "a", now=NOW)
    assert isinstance(first, QuizGradeResult)
    assert first.srs is not None and first.srs.interval_days == 1

    second = await service.grade_answer(user_id, expression_id, "b", now=NOW)
    assert isinstance(second, QuizGradeResult)
    assert second.srs is not None
    assert second.srs.interval_days == 1
    assert second.srs.ease_factor == 2.0  # 2.5 * 0.8
    assert second.srs.review_count == 0
    progress = await load_progress(session_factory, user_id, expression_id)
    assert progress.lapse_count == 1


# ---------------------------------------------------------------------------
# Fallbacks: the quiz never breaks
# ---------------------------------------------------------------------------


async def test_invalid_output_falls_back_and_touches_nothing(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9705)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([SCHEMA_INVALID_JSON]))

    result = await service.grade_answer(user_id, expression_id, "answer", now=NOW)

    assert isinstance(result, QuizUnavailable)
    assert result.reason == "invalid_output"
    progress = await load_progress(session_factory, user_id, expression_id)
    assert progress.review_count == 0  # schedule untouched
    assert progress.interval_days == 1  # initial state preserved
    assert await load_streak(session_factory, user_id) == 0


async def test_llm_error_falls_back(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    from app.integrations.llm.exceptions import LlmTransportError

    user_id = await prepare_user(session_factory, 9706)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(
        session_factory, FakeLlmClient(error=LlmTransportError("boom"))
    )

    result = await service.grade_answer(user_id, expression_id, "answer", now=NOW)

    assert isinstance(result, QuizUnavailable)
    assert result.reason == "llm_error"


async def test_llm_disabled_reports_and_calls_nothing(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9707)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, None)

    result = await service.grade_answer(user_id, expression_id, "answer", now=NOW)

    assert isinstance(result, QuizUnavailable)
    assert result.reason == "llm_disabled"
    assert await load_evaluations(session_factory, user_id) == []


async def test_answer_too_long_rejected_before_llm(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9708)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    llm = FakeLlmClient([CORRECT_JSON])
    service = make_service(session_factory, llm)

    result = await service.grade_answer(user_id, expression_id, "x" * 501, now=NOW)

    assert isinstance(result, QuizUnavailable)
    assert result.reason == "answer_too_long"
    assert llm.requests == []  # no LLM call paid


async def test_foreign_expression_is_not_found(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9709)
    foreign_id = await seed_served_expression(
        session_factory,
        await prepare_user(session_factory, 9710, fresh=False),
        "Not yours",
    )
    service = make_service(session_factory, FakeLlmClient([CORRECT_JSON]))

    result = await service.grade_answer(user_id, foreign_id, "answer", now=NOW)

    assert isinstance(result, QuizUnavailable)
    assert result.reason == "expression_not_found"


# ---------------------------------------------------------------------------
# Telemetry: metrics only, never the answer text
# ---------------------------------------------------------------------------


async def test_graded_answer_writes_telemetry_row(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9711)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([GRAMMAR_JSON]))

    await service.grade_answer(user_id, expression_id, "I break leg", now=NOW)

    rows = await load_evaluations(session_factory, user_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.is_correct is False
    assert row.error_type == "grammar"
    assert row.error_kind is None
    assert row.latency_ms >= 0
    # Metrics only: no answer text column exists in the telemetry model.
    assert "answer_text" not in QuizEvaluation.__table__.columns
    assert "answer" not in QuizEvaluation.__table__.columns


async def test_failed_evaluation_is_still_counted(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9712)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([SCHEMA_INVALID_JSON]))

    await service.grade_answer(user_id, expression_id, "answer", now=NOW)

    rows = await load_evaluations(session_factory, user_id)
    assert len(rows) == 1
    assert rows[0].is_correct is None
    assert rows[0].error_kind == "invalid_output"


async def load_streak(session_factory: SessionFactory, user_id: int) -> int:
    async with session_scope(session_factory) as session:
        result = await session.execute(
            select(Profile).where(Profile.user_id == user_id)
        )
        return result.scalar_one().quiz_success_streak


# ---------------------------------------------------------------------------
# Question selection
# ---------------------------------------------------------------------------


async def test_start_quiz_prefers_due_then_any_served(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9713)
    due_id = await seed_served_expression(
        session_factory, user_id, "Due phrase", translation="вопрос по графику"
    )
    async with session_scope(session_factory) as session:
        progress = await session.execute(
            select(ExpressionProgress).where(
                ExpressionProgress.user_id == user_id,
                ExpressionProgress.expression_id == due_id,
            )
        )
        row = progress.scalar_one()
        row.next_review_at = NOW - timedelta(days=1)  # overdue → due

    service = make_service(session_factory, FakeLlmClient([]))
    question = await service.start_quiz(user_id, now=NOW)

    assert isinstance(question, QuizQuestion)
    assert question.expression_id == due_id
    assert "вопрос по графику" in question.question_translation


async def test_start_quiz_without_words_is_no_words(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9714)
    service = make_service(session_factory, FakeLlmClient([]))

    question = await service.start_quiz(user_id, now=NOW)

    assert isinstance(question, QuizUnavailable)
    assert question.reason == "no_words"
