"""Formatting of lesson cards into Telegram HTML.

Pure presentation, mirroring :mod:`app.bot.profile_view`.
"""

from datetime import datetime, timedelta

from app.bot import texts
from app.db.models import Expression
from app.domain.languages import get_language_profile

_CARD_TEMPLATE = (
    "📚 Карточка {number} из {total} · {level} · {topic}\n"
    "\n"
    "<b>{text}</b>\n"
    "{ipa_block}"
    "{hint_block}"
    "🇷🇺 {translation}\n"
    "\n"
    "💬 {example}\n"
    "→ {example_translation}\n"
    "\n"
    "{disclaimer}"
)


def should_show_hint(
    *,
    created_at: datetime,
    is_difficult: bool,
    now: datetime,
    fade_after_weeks: int,
) -> bool:
    """Hint fading rule: fresh words or difficult words keep the hint.

    The hint is shown while ``created_at`` is younger than the fade
    threshold (N weeks, from config) or while the word is marked
    difficult; after the threshold it disappears unless difficult.
    """
    threshold = created_at + timedelta(weeks=fade_after_weeks)
    return now < threshold or is_difficult


def phonetic_hint_for(expression: Expression, accent: str | None) -> str:
    """The Russian approximation for the user's accent, falling back to
    the base hint. Accent-specific hints come from the DB columns; the
    fallback keeps the card rendering for words without variants."""
    if accent == "uk" and expression.phonetic_hint_ru_uk:
        return expression.phonetic_hint_ru_uk
    if accent == "us" and expression.phonetic_hint_ru_us:
        return expression.phonetic_hint_ru_us
    return expression.phonetic_hint_ru


def format_card(
    expression: Expression,
    *,
    index: int,
    total: int,
    accent: str | None = None,
    show_hint: bool = True,
) -> str:
    """Render one card; ``index`` is zero-based.

    Transcription display rules come from the language profile of the
    expression (config, not code): ``show_ipa`` toggles the IPA line,
    ``show_phonetic_hint`` the Russian approximation. The fading rule
    (``show_hint``) can hide the hint block on top of that; unknown
    languages get the permissive defaults (both lines shown).
    """
    language = get_language_profile(expression.language)
    hint_text = phonetic_hint_for(expression, accent)
    ipa_block = f"/{_escape(expression.ipa)}/\n" if language.show_ipa else ""
    hint_block = (
        f"🗣 {_escape(hint_text)}\n"
        if show_hint and language.show_phonetic_hint and hint_text
        else ""
    )
    return _CARD_TEMPLATE.format(
        number=index + 1,
        total=total,
        level=expression.level,
        topic=texts.TOPIC_LABELS.get(expression.topic, expression.topic),
        text=_escape(expression.text),
        ipa_block=ipa_block,
        hint_block=hint_block,
        translation=_escape(expression.translation),
        example=_escape(expression.example),
        example_translation=_escape(expression.example_translation),
        disclaimer=texts.PHONETIC_DISCLAIMER,
    )


def _escape(value: str) -> str:
    """Escape HTML-special characters in dictionary texts."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
