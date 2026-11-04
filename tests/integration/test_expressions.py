"""Integration tests for dictionary expressions on a real PostgreSQL."""

import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Expression
from app.db.seed import SEED_EXPRESSIONS, seed_expressions
from app.db.session import SessionFactory
from app.repositories import ExpressionRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]


async def insert_test_expression(
    db_session: AsyncSession,
    *,
    language: str = "ts",
    text_value: str,
    level: str = "A1",
    topic: str = "everyday",
    is_active: bool = True,
) -> Expression:
    expression = Expression(
        language=language,
        text=text_value,
        translation="перевод",
        ipa="aɪ piː eɪ",
        phonetic_hint_ru="пОдсказка",
        example="example",
        example_translation="пример",
        level=level,
        topic=topic,
        is_active=is_active,
    )
    db_session.add(expression)
    await db_session.flush()
    return expression


async def test_migration_creates_expressions_table(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'expressions'"
            )
        )
        columns = {row[0] for row in rows}
    assert {
        "id",
        "language",
        "text",
        "translation",
        "ipa",
        "phonetic_hint_ru",
        "example",
        "example_translation",
        "level",
        "topic",
        "is_active",
        "created_at",
        "updated_at",
    }.issubset(columns)


async def test_migration_creates_progress_table(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'expression_progress'"
            )
        )
        columns = {row[0] for row in rows}
    assert {
        "id",
        "user_id",
        "expression_id",
        "shown_at",
        "completed_at",
    }.issubset(columns)


async def test_migration_seeded_the_dictionary(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        result = await session.execute(
            select(Expression).where(Expression.language == "en")
        )
        expressions = list(result.scalars().all())
    assert len(expressions) == len(SEED_EXPRESSIONS)
    levels = {expression.level for expression in expressions}
    topics = {expression.topic for expression in expressions}
    assert {"A1", "A2", "B1", "B2"}.issubset(levels)
    assert len(topics) >= 4


async def test_seed_is_idempotent(
    migrated_db: None,
    session_factory: SessionFactory,
) -> None:
    """Running the seed again must not duplicate anything."""
    async with session_factory() as session:
        inserted = await seed_expressions(session)
        await session.commit()
        total = len(
            (
                await session.execute(
                    select(Expression).where(Expression.language == "en")
                )
            )
            .scalars()
            .all()
        )
    assert inserted == 0
    assert total == len(SEED_EXPRESSIONS)


async def test_duplicate_expression_is_rejected(db_session: AsyncSession) -> None:
    """The unique (language, text) constraint keeps the dictionary clean."""
    from sqlalchemy.exc import IntegrityError

    await insert_test_expression(db_session, text_value="dupe")

    with pytest.raises(IntegrityError):
        await insert_test_expression(db_session, text_value="dupe")

    await db_session.rollback()


async def test_get_active_filters(migrated_db: None, db_session: AsyncSession) -> None:
    repository = ExpressionRepository(db_session)
    await insert_test_expression(db_session, text_value="a1-one", level="A1")
    await insert_test_expression(db_session, text_value="a1-two", level="A1")
    await insert_test_expression(
        db_session, text_value="a2-one", level="A2", topic="sport"
    )
    await insert_test_expression(
        db_session, text_value="inactive", level="A1", is_active=False
    )
    # Same text under another language must not interfere.
    await insert_test_expression(
        db_session, language="ts2", text_value="a1-one", level="A1"
    )

    by_level = await repository.get_active(
        language="ts", level="A1", topics=None, limit=10
    )
    assert [expression.text for expression in by_level] == ["a1-one", "a1-two"]

    by_topic = await repository.get_active(
        language="ts", level="A2", topics=["sport"], limit=10
    )
    assert [expression.text for expression in by_topic] == ["a2-one"]

    limited = await repository.get_active(
        language="ts", level="A1", topics=None, limit=1
    )
    assert len(limited) == 1

    excluded = await repository.get_active(
        language="ts",
        level="A1",
        topics=None,
        limit=10,
        exclude_ids={by_level[0].id},
    )
    assert [expression.text for expression in excluded] == ["a1-two"]


async def test_count_active(migrated_db: None, db_session: AsyncSession) -> None:
    repository = ExpressionRepository(db_session)
    await insert_test_expression(db_session, text_value="one", level="A1")
    await insert_test_expression(db_session, text_value="two", level="A2")
    await insert_test_expression(
        db_session, text_value="three", level="A2", is_active=False
    )

    assert await repository.count_active(language="ts", topics=None) == 2
    assert await repository.count_active(language="ts", topics=None, level="A2") == 1
    assert await repository.count_active(language="ts", topics=["music"]) == 0


async def test_get_many_preserves_id_order(db_session: AsyncSession) -> None:
    repository = ExpressionRepository(db_session)
    first = await insert_test_expression(db_session, text_value="x1")
    second = await insert_test_expression(db_session, text_value="x2")

    result = await repository.get_many_by_ids([second.id, first.id])

    assert [expression.id for expression in result] == [
        first.id,
        second.id,
    ]
    assert await repository.get_many_by_ids([]) == []
