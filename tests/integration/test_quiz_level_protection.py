"""Integration tests for the quiz level-up protection on real PostgreSQL.

Rule under test: the CEFR level moves only after N correct quiz answers
in a row (config) AND the user's explicit confirmation. One good answer
never changes the level; any wrong answer resets the streak.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Expression, ExpressionProgress, Profile
from app.db.session import SessionFactory, session_scope
from app.services.quiz import QuizGradeResult, QuizService, QuizUnavailable
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
    '"explanation": "Верно.", "corrected": ""}'
)
WRONG_JSON = (
    '{"is_correct": false, "error_type": "grammar", '
    '"explanation": "Ошибка.", "corrected": "Break a leg"}'
)


async def prepare_user(
    session_factory: SessionFactory, telegram_id: int, level: str = "A1"
) -> int:
    async with session_scope(session_factory) as session:
        await reset_database(session)
        profile = await create_profile_row(
            session, telegram_id=telegram_id, level=level
        )
        return profile.user_id


async def seed_served_expression(
    session_factory: SessionFactory, user_id: int, text: str
) -> int:
    async with session_scope(session_factory) as session:
        expression = Expression(
            language="ts",
            text=text,
            translation="перевод",
            ipa="aɪ piː eɪ",
            phonetic_hint_ru="пОдсказка",
            example="example",
            example_translation="пример",
            level="A1",
            topic="everyday",
        )
        session.add(expression)
        await session.flush()
        session.add(ExpressionProgress(user_id=user_id, expression_id=expression.id))
        await session.flush()
        return expression.id


async def load_profile(session_factory: SessionFactory, user_id: int) -> Profile:
    async with session_scope(session_factory) as session:
        result = await session.execute(
            select(Profile).where(Profile.user_id == user_id)
        )
        return result.scalar_one()


def make_service(
    session_factory: SessionFactory, llm: object, *, streak_needed: int = 3
) -> QuizService:
    settings = make_settings(
        database_url="postgresql://u:p@h/db", quiz_level_streak_needed=streak_needed
    )
    return QuizService(session_factory, llm, settings)  # type: ignore[arg-type]


async def answer(
    service: QuizService,
    session_factory: SessionFactory,
    user_id: int,
    expression_id: int,
    payload: str,
) -> QuizGradeResult | QuizUnavailable:
    llm = FakeLlmClient([payload])
    # Replace the client for every call: one preset answer per call.
    service._llm_client = llm
    return await service.grade_answer(user_id, expression_id, "answer", now=NOW)


async def test_one_correct_answer_never_changes_level(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9800)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([CORRECT_JSON]))

    result = await service.grade_answer(user_id, expression_id, "answer", now=NOW)

    assert isinstance(result, QuizGradeResult)
    assert result.is_correct is True
    assert result.streak == 1
    assert result.level_offer is False  # nothing to confirm yet
    profile = await load_profile(session_factory, user_id)
    assert profile.level == "A1"  # unchanged
    assert profile.quiz_success_streak == 1


async def test_streak_needed_correct_answers_offer_but_do_not_promote(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9801)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([CORRECT_JSON] * 3))

    offers: list[bool] = []
    for step in range(3):
        result = await service.grade_answer(
            user_id, expression_id, "answer", now=NOW + timedelta(days=step)
        )
        assert isinstance(result, QuizGradeResult)
        offers.append(result.level_offer)

    assert offers == [False, False, True]  # offered exactly at N=3
    profile = await load_profile(session_factory, user_id)
    assert profile.level == "A1"  # still unchanged until confirmed
    assert profile.quiz_success_streak == 3


async def test_wrong_answer_resets_the_streak(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9802)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([]))

    await answer(service, session_factory, user_id, expression_id, CORRECT_JSON)
    await answer(service, session_factory, user_id, expression_id, CORRECT_JSON)
    result = await answer(service, session_factory, user_id, expression_id, WRONG_JSON)
    assert isinstance(result, QuizGradeResult)
    assert result.streak == 0
    assert result.level_offer is False

    profile = await load_profile(session_factory, user_id)
    assert profile.quiz_success_streak == 0
    assert profile.level == "A1"
    # The reset streak cannot confirm a promotion anymore.
    assert await service.confirm_level_up(user_id) is None
    assert (await load_profile(session_factory, user_id)).level == "A1"


async def test_confirmed_promotion_applies_and_resets_streak(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9803)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([CORRECT_JSON] * 3))
    for step in range(3):
        await service.grade_answer(
            user_id, expression_id, "answer", now=NOW + timedelta(days=step)
        )

    new_level = await service.confirm_level_up(user_id)

    assert new_level == "A2"
    profile = await load_profile(session_factory, user_id)
    assert profile.level == "A2"
    assert profile.quiz_success_streak == 0
    # A second confirmation without a new streak does nothing.
    assert await service.confirm_level_up(user_id) is None
    assert (await load_profile(session_factory, user_id)).level == "A2"


async def test_configurable_threshold(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9804)
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([]), streak_needed=2)

    first = await answer(service, session_factory, user_id, expression_id, CORRECT_JSON)
    assert isinstance(first, QuizGradeResult)
    assert first.level_offer is False
    second = await answer(
        service, session_factory, user_id, expression_id, CORRECT_JSON
    )
    assert isinstance(second, QuizGradeResult)
    assert second.level_offer is True
    assert await service.confirm_level_up(user_id) == "A2"


async def test_top_level_c2_is_never_offered(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    user_id = await prepare_user(session_factory, 9805, level="C2")
    expression_id = await seed_served_expression(
        session_factory, user_id, "Break a leg"
    )
    service = make_service(session_factory, FakeLlmClient([CORRECT_JSON] * 3))
    for step in range(3):
        result = await service.grade_answer(
            user_id, expression_id, "answer", now=NOW + timedelta(days=step)
        )
        assert isinstance(result, QuizGradeResult)
        assert result.level_offer is False

    assert await service.confirm_level_up(user_id) is None
    assert (await load_profile(session_factory, user_id)).level == "C2"
