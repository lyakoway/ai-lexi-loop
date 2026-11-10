"""Language profiles: adding a language is a config entry, not new code.

Every supported target language is described by one :class:`LanguageProfile`
record: display label, the language explanations are written in, TTS voices
per accent, transcription display rules, a short grammar briefing for
prompts and the diagnostic (placement test) size.

Out of the box the product ships English (US/UK accents). Spanish is
included as a worked second-language example: the architecture accepts it
without code changes, but the seed catalogue, curated hints and content
quality are English-first — Spanish cards come from AI generation and
their quality is not guaranteed yet. No other language should be promised
to users until a profile entry with real content exists.

Display labels for languages live here (not in the bot texts module): the
registry is the single source of truth for everything language-bound.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageProfile:
    """Everything the product needs to know about one target language."""

    code: str
    name: str
    # Bot display label (flag + name) used in keyboards and profile view.
    label: str
    # The language tutor explanations are written in (product audience
    # is Russian-speaking; override per language if that ever changes).
    explanation_language: str
    # Accent -> TTS voice mapping; accents beyond the profile's
    # ``supported_accents`` fall back to the first entry.
    tts_voices: dict[str, str]
    # Transcription display rules on lesson cards.
    show_ipa: bool
    show_phonetic_hint: bool
    # Short grammar briefing injected into language-aware prompts.
    grammar_notes: str
    # Level settings: the placement test length for this language.
    diagnostic_question_count: int
    supported_accents: tuple[str, ...] = ("us", "uk")

    def voice_for(self, accent: str) -> str:
        """The configured TTS voice for ``accent`` (first entry as
        fallback for unsupported accents)."""
        return self.tts_voices.get(accent, next(iter(self.tts_voices.values())))


ENGLISH = LanguageProfile(
    code="en",
    name="English",
    label="🇬🇧 English",
    explanation_language="ru",
    tts_voices={"us": "alloy", "uk": "onyx"},
    show_ipa=True,
    show_phonetic_hint=True,
    grammar_notes=(
        "Word order is fixed (subject-verb-object); questions use "
        "auxiliary verbs (do/does/did); articles a/an/the are mandatory; "
        "phrasal verbs and idioms are extremely common in everyday speech."
    ),
    diagnostic_question_count=7,
    supported_accents=("us", "uk"),
)

# Worked second-language example (see module docstring): the config
# entry is complete, but content quality is not guaranteed yet.
SPANISH = LanguageProfile(
    code="es",
    name="Spanish",
    label="🇪🇸 Español",
    explanation_language="ru",
    tts_voices={"us": "alloy"},
    show_ipa=True,
    show_phonetic_hint=False,
    grammar_notes=(
        "Verbs conjugate by person and tense; nouns have grammatical "
        "gender (el/la); adjectives agree with nouns and usually follow "
        "them; ser vs estar distinguish permanent and temporary states."
    ),
    diagnostic_question_count=5,
    supported_accents=(),  # no meaningful US/UK split for Spanish
)

LANGUAGE_PROFILES: dict[str, LanguageProfile] = {
    ENGLISH.code: ENGLISH,
    SPANISH.code: SPANISH,
}

#: Fallback for languages without a profile entry: permissive defaults
#: so the product never crashes on a new code.
GENERIC_PROFILE = LanguageProfile(
    code="",
    name="the target language",
    label="🌐",
    explanation_language="ru",
    tts_voices={"us": "alloy"},
    show_ipa=True,
    show_phonetic_hint=True,
    grammar_notes="",
    diagnostic_question_count=5,
    supported_accents=("us", "uk"),
)


def get_language_profile(code: str) -> LanguageProfile:
    """The profile for ``code``, or the permissive generic profile."""
    return LANGUAGE_PROFILES.get(code.lower(), GENERIC_PROFILE)


def supported_target_languages() -> tuple[LanguageProfile, ...]:
    """Languages offered to learners, registry order."""
    return tuple(LANGUAGE_PROFILES.values())


def language_label(code: str) -> str:
    """Display label for a language code (native languages included)."""
    special_labels: dict[str, str] = {"ru": "🇷🇺 Русский"}
    if code in special_labels:
        return special_labels[code]
    return get_language_profile(code).label
