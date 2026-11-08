"""Integration tests for AI expression generation (Stage 6.3).

Real PostgreSQL: drafts persistence, acceptance into the catalogue with
owner scoping, SRS scheduling via the existing progress machinery,
user isolation, and proof that the shared catalogue stays clean.
"""

import os
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AnalyticsEvent,
    Expression,
    ExpressionProgress,
    Profile,
    UserGeneratedExpression,
)
from app.db.session import SessionFactory, session_scope
from app.repositories import (
    ExpressionRepository,
    ProfileRepository,
    UserGeneratedExpressionRepository,
)
from tests.conftest import FakeLlmClient
from tests.integration.conftest import (
    create_profile_row,
    make_analytics,
    reset_database,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is not set; requires PostgreSQL",
    ),
]

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def generated_json(texts: list[str]) -> str:
    import json

    items = [
        {
            "text": text,
            "translation": f"перевод {number}",
            "ipa": "aɪ piː eɪ",
            "phonetic_hint_ru": "пОдсказка",
            "example": f"Example {number} with {text}.",
            "example_translation": f"Пример {number}.",
            "level": "A1",
            "topic": "everyday",
        }
        for number, text in enumerate(texts, start=1)
    ]
    return json.dumps({"expressions": items}, ensure_ascii=False)


def make_service(session: AsyncSession, llm_client: Any) -> Any:
    """GenerationService wired for integration tests."""
    from app.services import ProfileService
    from app.services.tutor.generation import GenerationService

    return GenerationService(
        ProfileService(ProfileRepository(session), make_analytics(session)),
        ExpressionRepository(session),
        UserGeneratedExpressionRepository(session),
        llm_client,
        make_analytics(session),
    )


async def prepare_user(session_factory: SessionFactory, telegram_id: int) -> Profile:
    async with session_scope(session_factory) as session:
        await reset_database(session)
        return await create_profile_row(session, telegram_id=telegram_id)


async def generate_one(
    session_factory: SessionFactory, user_id: int, text: str
) -> tuple[int, str]:
    """Generate a single draft and return (draft_id, text) immediately."""
    async with session_scope(session_factory) as session:
        llm = FakeLlmClient([generated_json([text])])
        result = await make_service(session, llm).generate_drafts(
            user_id, topic="everyday", count=1
        )
        assert not hasattr(result, "reason")
        draft = result.drafts[0]
        return draft.id, draft.text


async def test_generation_saves_isolated_drafts(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Drafts belong to the generating user; the catalogue stays clean."""
    profile = await prepare_user(session_factory, 9100)
    async with session_scope(session_factory) as session:
        llm = FakeLlmClient([generated_json(["Piece of cake", "Break a leg!"])])
        result = await make_service(session, llm).generate_drafts(
            profile.user_id, topic="everyday", count=2
        )
        assert [draft.text for draft in result.drafts] == [
            "Piece of cake",
            "Break a leg!",
        ]
        assert all(draft.user_id == profile.user_id for draft in result.drafts)
        assert all(draft.status == "draft" for draft in result.drafts)

        shared_cards = (
            (
                await session.execute(
                    select(Expression).where(
                        Expression.owner_user_id.is_(None), Expression.language == "en"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(shared_cards) == 16  # seed set only

        owned = (
            (
                await session.execute(
                    select(Expression).where(
                        Expression.owner_user_id == profile.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert owned == []  # nothing accepted yet

    async with session_scope(session_factory) as session:
        rows = (await session.execute(select(UserGeneratedExpression))).scalars().all()
        assert len(rows) == 2
        assert all(row.user_id == profile.user_id for row in rows)


async def test_accept_is_idempotent(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    profile = await prepare_user(session_factory, 9103)
    draft_id, _ = await generate_one(
        session_factory, profile.user_id, "No pain no gain"
    )

    async with session_scope(session_factory) as session:
        await make_service(session, FakeLlmClient([])).accept_drafts(
            profile.user_id, [draft_id]
        )
    async with session_scope(session_factory) as session:
        second = await make_service(session, FakeLlmClient([])).accept_drafts(
            profile.user_id, [draft_id]
        )

    assert second.accepted == []
    assert second.skipped_ids == [draft_id]

    async with session_scope(session_factory) as session:
        cards = (
            (
                await session.execute(
                    select(Expression).where(
                        Expression.owner_user_id == profile.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        progress_rows = (
            (
                await session.execute(
                    select(ExpressionProgress).where(
                        ExpressionProgress.user_id == profile.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
    # One card, still without progress: SRS starts in the first lesson.
    assert len(cards) == 1
    assert len(progress_rows) == 0


async def test_catalogue_duplicate_is_filtered_at_generation(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """An expression identical to a catalogue card is never drafted."""
    profile = await prepare_user(session_factory, 9104)
    async with session_scope(session_factory) as session:
        session.add(
            Expression(
                language="ts",
                text="Good morning!",
                translation="Доброе утро!",
                ipa="ɡʊd ˈmɔːrnɪŋ",
                phonetic_hint_ru="гуд мОрнинг",
                example="Good morning! Did you sleep well?",
                example_translation="Доброе утро!",
                level="A1",
                topic="everyday",
            )
        )
        await session.flush()
        llm = FakeLlmClient([generated_json(["Good morning!"])])
        result = await make_service(session, llm).generate_drafts(
            profile.user_id, topic="everyday", count=1
        )
        assert result.drafts == []
        assert result.duplicates_skipped == 1


async def test_rejected_drafts_never_enter_learning(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    profile = await prepare_user(session_factory, 9105)
    draft_id, _ = await generate_one(session_factory, profile.user_id, "Call it a day")

    async with session_scope(session_factory) as session:
        rejected = await make_service(session, FakeLlmClient([])).reject_drafts(
            profile.user_id, [draft_id]
        )
        assert rejected == 1

        cards = (
            (
                await session.execute(
                    select(Expression).where(
                        Expression.owner_user_id == profile.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        progress_rows = (
            (
                await session.execute(
                    select(ExpressionProgress).where(
                        ExpressionProgress.user_id == profile.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        statuses = {
            row.status
            for row in (
                await session.execute(
                    select(UserGeneratedExpression).where(
                        UserGeneratedExpression.user_id == profile.user_id
                    )
                )
            )
            .scalars()
            .all()
        }
    assert cards == []
    assert progress_rows == []
    assert statuses == {"rejected"}


async def test_accepted_card_visible_to_owner_only(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Owner scoping: personal cards enter only their owner's lessons."""
    profile = await prepare_user(session_factory, 9106)
    draft_id, _ = await generate_one(
        session_factory, profile.user_id, "Sooner or later"
    )
    async with session_scope(session_factory) as session:
        accept_result = await make_service(session, FakeLlmClient([])).accept_drafts(
            profile.user_id, [draft_id]
        )
        card_id: int = accept_result.accepted[0].id

    async with session_scope(session_factory) as session:
        repository = ExpressionRepository(session)
        mine = await repository.get_active(
            language="ts",
            level="A1",
            topics=["everyday"],
            limit=20,
            owner_user_id=profile.user_id,
        )
        assert card_id in {expression.id for expression in mine}

        stranger_view = await repository.get_active(
            language="ts",
            level="A1",
            topics=["everyday"],
            limit=20,
            owner_user_id=999999,
        )
        assert card_id not in {expression.id for expression in stranger_view}


async def test_generation_and_acceptance_events(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    profile = await prepare_user(session_factory, 9107)
    draft_id, _ = await generate_one(
        session_factory, profile.user_id, "Under the weather"
    )

    async with session_scope(session_factory) as session:
        await make_service(session, FakeLlmClient([])).accept_drafts(
            profile.user_id, [draft_id], source="web"
        )
        events = (
            (
                await session.execute(
                    select(AnalyticsEvent).where(
                        AnalyticsEvent.user_id == profile.user_id,
                        AnalyticsEvent.event_name.in_(
                            ["tutor_generated", "tutor_accepted"]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
    sources = {event.event_name: event.source for event in events}
    assert sources.get("tutor_generated") == "telegram"
    assert sources.get("tutor_accepted") == "web"


async def test_lesson_serves_accepted_personal_cards(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """End-to-end: accepted personal cards enter the owner's lessons.

    The served card gets standard SRS scheduling through the lesson
    flow, and the other user never sees it.
    """
    from app.services.lessons import LessonPlan
    from tests.integration.conftest import make_lesson_service

    profile = await prepare_user(session_factory, 9108)
    draft_id, draft_text = await generate_one(
        session_factory, profile.user_id, "Once in a blue moon"
    )
    async with session_scope(session_factory) as session:
        accept_result = await make_service(session, FakeLlmClient([])).accept_drafts(
            profile.user_id, [draft_id], source="web"
        )
        card_id: int = accept_result.accepted[0].id

    async with session_scope(session_factory) as session:
        refreshed = (
            await session.execute(
                select(Profile).where(Profile.user_id == profile.user_id)
            )
        ).scalar_one()
        plan = await make_lesson_service(session).build_lesson(
            profile.user_id, refreshed, now=NOW
        )
        assert isinstance(plan, LessonPlan)
        assert [expression.id for expression in plan.expressions] == [card_id]

        # Standard SRS scheduling happened through the lesson flow.
        progress = (
            await session.execute(
                select(ExpressionProgress).where(
                    ExpressionProgress.user_id == profile.user_id,
                    ExpressionProgress.expression_id == card_id,
                )
            )
        ).scalar_one()
        assert progress.review_count == 0
        assert progress.next_review_at is not None
        assert draft_text


# ---------------------------------------------------------------------------
# Isolation & concurrency regressions (final review fixes)
# ---------------------------------------------------------------------------


async def test_accept_matching_another_users_personal_card_creates_own(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Isolation: a shared-text match with someone's *personal* card
    must create the accepting user's own card, never attach progress to
    a foreign one."""
    owner = await prepare_user(session_factory, 9110)
    draft_id, _text = await generate_one(session_factory, owner.user_id, "Chill out")
    async with session_scope(session_factory) as session:
        accept_result = await make_service(session, FakeLlmClient([])).accept_drafts(
            owner.user_id, [draft_id]
        )
        foreign_card_id: int = accept_result.accepted[0].id

    # Another user generates and accepts the same expression.
    # No database reset for the second user: the foreign personal card
    # must stay alive for this isolation check.
    async with session_scope(session_factory) as session:
        other = await create_profile_row(session, telegram_id=9111)
    other_draft_id, _ = await generate_one(session_factory, other.user_id, "chill  out")
    async with session_scope(session_factory) as session:
        accept_result = await make_service(session, FakeLlmClient([])).accept_drafts(
            other.user_id, [other_draft_id], source="web"
        )
        assert len(accept_result.accepted) == 1
        own_card = accept_result.accepted[0]
        assert own_card.id != foreign_card_id  # never the foreign card
        assert own_card.owner_user_id == other.user_id

    async with session_scope(session_factory) as session:
        # No progress at accept time; and the other user's catalogue
        # view never contains the foreign personal card.
        progress_rows = (
            (
                await session.execute(
                    select(ExpressionProgress).where(
                        ExpressionProgress.user_id == other.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert progress_rows == []
        visible_to_other = (
            (
                await session.execute(
                    select(Expression.id).where(
                        Expression.owner_user_id == owner.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert foreign_card_id in visible_to_other
        assert visible_to_other == [foreign_card_id]


async def test_same_user_cannot_duplicate_personal_text(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """The personal partial unique index blocks duplicates (any case)."""
    from sqlalchemy.exc import IntegrityError

    profile = await prepare_user(session_factory, 9112)
    async with session_scope(session_factory) as session:
        session.add(
            Expression(
                owner_user_id=profile.user_id,
                language="ts",
                text="Way to go!",
                translation="перевод",
                ipa="aɪ piː eɪ",
                phonetic_hint_ru="пОдсказка",
                example="example",
                example_translation="пример",
                level="A1",
                topic="everyday",
            )
        )
        await session.flush()

        session.add(
            Expression(
                owner_user_id=profile.user_id,
                language="ts",
                text="way to GO!",  # same, different case
                translation="перевод",
                ipa="aɪ piː eɪ",
                phonetic_hint_ru="пОдсказка",
                example="example",
                example_translation="пример",
                level="A1",
                topic="everyday",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_generation_filters_catalogue_duplicate_case_insensitive(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    profile = await prepare_user(session_factory, 9113)
    async with session_scope(session_factory) as session:
        session.add(
            Expression(
                language="ts",
                text="Break a leg!",
                translation="Доброе утро!",
                ipa="aɪ piː eɪ",
                phonetic_hint_ru="пОдсказка",
                example="example",
                example_translation="пример",
                level="A1",
                topic="everyday",
            )
        )
        await session.flush()
        llm = FakeLlmClient([generated_json(["BREAK A LEG!"])])
        result = await make_service(session, llm).generate_drafts(
            profile.user_id, topic="everyday", count=1
        )
        assert result.drafts == []
        assert result.duplicates_skipped == 1
