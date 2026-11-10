"""Unit tests for language profiles (Stage 6.4.7).

Adding a language is a registry entry, not code: these tests pin the
contract (EN complete, ES as the worked second-language example, generic
fallback) and the transcription display rules on lesson cards.
"""

from app.bot.lesson_view import format_card
from app.db.models import Expression
from app.domain.languages import (
    ENGLISH,
    GENERIC_PROFILE,
    LANGUAGE_PROFILES,
    SPANISH,
    get_language_profile,
    language_label,
    supported_target_languages,
)


def make_expression(language: str = "en") -> Expression:
    return Expression(
        language=language,
        text="Break a leg",
        translation="удачи",
        ipa="breɪk ə leɡ",
        phonetic_hint_ru="брэйк э лэг",
        example="Break a leg at the audition!",
        example_translation="Удачи на прослушивании!",
        level="A1",
        topic="everyday",
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_english_profile_is_complete() -> None:
    assert ENGLISH.code == "en"
    assert ENGLISH.show_ipa is True
    assert ENGLISH.show_phonetic_hint is True
    assert ENGLISH.tts_voices["us"] != ENGLISH.tts_voices["uk"]
    assert 5 <= ENGLISH.diagnostic_question_count <= 7
    assert ENGLISH.grammar_notes


def test_second_language_connects_via_config_only() -> None:
    """ES exists as a registry entry with all fields a language needs."""
    assert SPANISH.code == "es"
    assert "es" in LANGUAGE_PROFILES
    assert 5 <= SPANISH.diagnostic_question_count <= 7
    assert SPANISH.grammar_notes
    # The onboarding/settings choice is derived from the registry.
    codes = [profile.code for profile in supported_target_languages()]
    assert codes == ["en", "es"]


def test_unknown_language_falls_back_to_generic_profile() -> None:
    profile = get_language_profile("de")
    assert profile is GENERIC_PROFILE
    # Permissive defaults: the product keeps working for new codes.
    assert profile.show_ipa is True
    assert profile.voice_for("us")


def test_voice_for_accent_with_fallback() -> None:
    assert ENGLISH.voice_for("us") == "alloy"
    assert ENGLISH.voice_for("uk") == "onyx"
    # Unsupported accents fall back to the first configured voice.
    assert SPANISH.voice_for("uk") == "alloy"
    assert GENERIC_PROFILE.voice_for("uk") == "alloy"


def test_language_label_from_registry() -> None:
    assert language_label("en") == "🇬🇧 English"
    assert language_label("es") == "🇪🇸 Español"
    assert language_label("ru") == "🇷🇺 Русский"
    assert language_label("zz") == "🌐"


# ---------------------------------------------------------------------------
# Transcription display rules on cards
# ---------------------------------------------------------------------------


def test_english_card_shows_ipa_and_hint() -> None:
    card = format_card(make_expression("en"), index=0, total=1)
    assert "/breɪk ə leɡ/" in card
    assert "🗣" in card and "брэйк э лэг" in card


def test_spanish_card_rules_come_from_config() -> None:
    """ES: IPA stays, the Russian approximation hint is suppressed."""
    card = format_card(make_expression("es"), index=0, total=1)
    assert "/breɪk ə leɡ/" in card
    assert "🗣" not in card
    assert "брэйк э лэг" not in card
