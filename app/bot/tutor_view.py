"""Formatting of tutor explanations into Telegram HTML.

Every text produced by the model is escaped: LLM output is untrusted
content and must never be rendered as HTML markup.
"""

from app.bot import texts
from app.bot.lesson_view import _escape
from app.services.tutor.outputs import ExplanationOut

#: Telegram hard-caps a message at 4096 characters; keep a margin for
#: the caption and the ellipsis so edit_text never rejects the view.
_MAX_VIEW_LENGTH = 3900
_TRUNCATION_SUFFIX = "…"


def format_explanation(expression_text: str, explanation: ExplanationOut) -> str:
    """Render one explanation view."""
    parts = [
        f"🧠 <b>{_escape(expression_text)}</b>\n<i>{texts.TUTOR_AI_NOTE}</i>",
        f"📌 <b>Значение</b>\n{_escape(explanation.meaning)}",
        f"💡 <b>Как употребляется</b>\n{_escape(explanation.explanation)}",
    ]
    if explanation.usage_notes:
        notes = "\n".join(f"• {_escape(note)}" for note in explanation.usage_notes)
        parts.append(f"🔎 <b>Особенности</b>\n{notes}")
    if explanation.common_mistakes:
        mistakes = "\n".join(
            f"• {_escape(mistake)}" for mistake in explanation.common_mistakes
        )
        parts.append(f"⚠️ <b>Частые ошибки</b>\n{mistakes}")
    examples = "\n\n".join(
        f"✍️ {_escape(example.text)}\n→ {_escape(example.translation)}"
        for example in explanation.examples
    )
    parts.append(f"<b>Примеры</b>\n{examples}")
    if explanation.mini_dialogue is not None:
        lines = "\n".join(
            f"— {_escape(line.text)}\n  ({_escape(line.translation)})"
            for line in explanation.mini_dialogue.lines
        )
        parts.append(f"💬 <b>Мини-диалог</b>\n{lines}")
    return _fit_to_telegram("\n\n".join(parts))


def _fit_to_telegram(text: str) -> str:
    """Clamp the rendered view to the Telegram message limit."""
    if len(text) <= _MAX_VIEW_LENGTH:
        return text
    cut = _MAX_VIEW_LENGTH - len(_TRUNCATION_SUFFIX)
    return text[:cut].rstrip() + _TRUNCATION_SUFFIX
