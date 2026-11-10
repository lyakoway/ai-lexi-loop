"""Integration smoke for the second language (ES) connected via config.

The honest scope: the architecture accepts Spanish without code changes
(profile, placement, generation prompts, TTS voices, card rendering all
read the language profile), but content quality is English-first — the
seed catalogue stays English-only and lessons for ES stay gracefully
empty until ES cards exist (AI drafts accepted through /generate).
"""

import asyncio
import json
import os

import pytest

from app.db.session import SessionFactory, session_scope
from app.domain.languages import SPANISH, get_language_profile
from app.domain.placement import recommend_level
from app.domain.srs import utc_now
from app.repositories import (
    ExpressionProgressRepository,
    ExpressionRepository,
    ProfileRepository,
    UserRepository,
)
from app.schemas.profile import ProfileCreate
from app.services.lessons import LessonService, LessonUnavailable
from app.services.placement import PlacementService, PlacementSet
from app.services.placement_prompts import build_placement_messages
from app.services.profiles import ProfileService
from app.services.tutor.prompts import build_generation_messages
from tests.conftest import FakeLlmClient, make_settings
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


def es_profile_payload() -> dict[str, object]:
    return {
        "native_language": "ru",
        "target_language": "es",
        "level": "A1",
        "learning_goal": "travel",
        "topics": ["travel"],
        "preferred_accent": "us",
        "daily_study_minutes": 15,
        "new_items_per_day": 5,
        "lesson_time": "08:00",
        "review_time": "19:00",
        "timezone": "UTC",
    }


async def test_es_profile_saves_and_loads_via_config(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """A user can be onboarded to ES purely through config values."""
    async with session_scope(session_factory) as session:
        await reset_database(session)
        user, _ = await UserRepository(session).get_or_create_by_identity(
            "telegram", "10101"
        )
        saved = await ProfileService(
            ProfileRepository(session), make_analytics(session)
        ).save_profile(user.id, ProfileCreate.model_validate(es_profile_payload()))
        assert saved.target_language == "es"

    async with session_scope(session_factory) as session:
        profile = await ProfileRepository(session).get_by_user_id(user.id)
    assert profile is not None
    assert profile.target_language == "es"
    assert profile.level == "A1"


async def test_es_lesson_is_gracefully_empty_until_cards_exist(
    migrated_db: None, session_factory: SessionFactory
) -> None:
    """Honest scope: the EN-only catalogue yields no ES lessons yet."""
    async with session_scope(session_factory) as session:
        await reset_database(session)
        profile = await create_profile_row(session, telegram_id=10102)
        profile.target_language = "es"
        user_id = profile.user_id

    async with session_scope(session_factory) as session:
        stored_profile = await ProfileRepository(session).get_by_user_id(user_id)
        assert stored_profile is not None
        lesson = await LessonService(
            ExpressionRepository(session),
            ExpressionProgressRepository(session),
            make_analytics(session),
        ).build_lesson(user_id, stored_profile, now=utc_now())

    assert isinstance(lesson, LessonUnavailable)


def test_es_language_profile_drives_prompts_and_voices() -> None:
    """Config-only wiring: prompt text, question count and TTS voice."""
    profile = get_language_profile("es")
    assert profile is SPANISH

    system, user = build_placement_messages(profile=profile, native_language="ru")
    assert "exactly 5 questions" in system  # ES diagnostic size from config
    assert "Spanish" in user
    assert profile.grammar_notes in user  # grammar briefing from config

    # EN stays at its own configured size.
    english = get_language_profile("en")
    en_system, _ = build_placement_messages(profile=english, native_language="ru")
    assert "exactly 7 questions" in en_system

    # TTS voices per language profile.
    assert profile.voice_for("us") == "alloy"
    assert english.voice_for("uk") == "onyx"

    # The scoring rule is language-agnostic (no code change needed).
    assert recommend_level([("A2", True), ("B1", True), ("B2", False)]) == "B1"


def test_es_generation_prompt_uses_language_name() -> None:
    system, _ = build_generation_messages(
        count=3,
        topic="travel",
        level="A1",
        native_language="ru",
        target_language="es",
        situation="",
    )
    assert "Spanish" in system


def test_placement_service_generates_for_es_with_fake_llm() -> None:
    """Placement generation is language-parametric end to end (smoke)."""
    payload = json.dumps(
        {
            "questions": [
                {
                    "question": "¿Cómo te llamas?",
                    "options": ["Me llamo Ana", "Soy Ana", "Estoy Ana", "Tengo Ana"],
                    "correct_index": 0,
                    "level": "A2",
                },
                {
                    "question": "Yo ___ en Madrid.",
                    "options": ["vivo", "vives", "vive", "vivimos"],
                    "correct_index": 0,
                    "level": "A2",
                },
            ]
        }
    )
    settings = make_settings(database_url="postgresql://u:p@h/db")
    service = PlacementService(FakeLlmClient([payload]), settings)
    placement = asyncio.run(
        service.generate(target_language="es", native_language="ru")
    )
    assert isinstance(placement, PlacementSet)
    assert placement.size == 2
    assert placement.questions[0].options[0].startswith("Me llamo")
