"""Formatting of lesson cards into Telegram HTML.

Pure presentation, mirroring :mod:`app.bot.profile_view`.
"""

from app.bot import texts
from app.db.models import Expression

_CARD_TEMPLATE = (
    "📚 Карточка {number} из {total} · {level} · {topic}\n"
    "\n"
    "<b>{text}</b>\n"
    "/{ipa}/\n"
    "🗣 {hint}\n"
    "🇷🇺 {translation}\n"
    "\n"
    "💬 {example}\n"
    "→ {example_translation}\n"
    "\n"
    "{disclaimer}"
)


def format_card(expression: Expression, *, index: int, total: int) -> str:
    """Render one card; ``index`` is zero-based."""
    return _CARD_TEMPLATE.format(
        number=index + 1,
        total=total,
        level=expression.level,
        topic=texts.TOPIC_LABELS.get(expression.topic, expression.topic),
        text=_escape(expression.text),
        ipa=_escape(expression.ipa),
        hint=_escape(expression.phonetic_hint_ru),
        translation=_escape(expression.translation),
        example=_escape(expression.example),
        example_translation=_escape(expression.example_translation),
        disclaimer=texts.PHONETIC_DISCLAIMER,
    )


def _escape(value: str) -> str:
    """Escape HTML-special characters in dictionary texts."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
