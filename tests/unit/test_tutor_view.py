"""Tests for tutor view rendering and keyboard helpers."""

from app.bot import texts
from app.bot.keyboards.tutor import back_to_card_keyboard, explain_button
from app.bot.tutor_view import format_explanation
from app.services.tutor.outputs import ExplanationOut


def make_explanation(**overrides: object) -> ExplanationOut:
    values: dict[str, object] = {
        "meaning": "Ни пуха ни пера!",
        "explanation": "Идиоматическое пожелание удачи.",
        "usage_notes": ["Неформально", "Дружеское"],
        "common_mistakes": ["Дословный перевод legs"],
        "examples": [{"text": "Break a leg!", "translation": "Ни пуха!"}],
        "mini_dialogue": {
            "lines": [
                {"text": "I have an exam.", "translation": "У меня экзамен."},
                {"text": "Break a leg!", "translation": "Удачи!"},
            ]
        },
    }
    values.update(overrides)
    return ExplanationOut.model_validate(values)


def test_explanation_view_shows_all_sections() -> None:
    rendered = format_explanation("Break a leg!", make_explanation())
    assert "Break a leg!" in rendered
    assert texts.TUTOR_AI_NOTE in rendered
    assert "Ни пуха ни пера!" in rendered
    assert "Идиоматическое пожелание удачи." in rendered
    assert "Особенности" in rendered
    assert "Частые ошибки" in rendered
    assert "Примеры" in rendered
    assert "Мини-диалог" in rendered


def test_explanation_view_escapes_model_text() -> None:
    rendered = format_explanation(
        "x <b>",
        make_explanation(explanation="evil <script> & quote"),
    )
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "x &lt;b&gt;" in rendered


def test_optional_sections_are_omitted() -> None:
    rendered = format_explanation(
        "Break a leg!",
        make_explanation(usage_notes=[], common_mistakes=[], mini_dialogue=None),
    )
    assert "Особенности" not in rendered
    assert "Частые ошибки" not in rendered
    assert "Мини-диалог" not in rendered


def test_explain_button_callback_format() -> None:
    assert explain_button(token=7, index=2).callback_data == "tut~explain~7~2"


def test_back_keyboard_callback_format() -> None:
    keyboard = back_to_card_keyboard(token=7, index=2)
    assert keyboard.inline_keyboard[0][0].callback_data == "tut~back~7~2"
    assert keyboard.inline_keyboard[0][0].text == texts.BTN_BACK_TO_CARD


def test_overlong_explanation_is_clamped_to_telegram_limit() -> None:
    """A maximal LLM answer must not exceed the Telegram message cap."""
    # Maximal within the schema limits (~10k chars when rendered).
    long_explanation = ExplanationOut.model_validate(
        {
            "meaning": "значение " * 33,  # 297 chars
            "explanation": "слово " * 250,  # 1500 chars
            "usage_notes": ["заметка " * 37] * 5,
            "common_mistakes": ["ошибка " * 37] * 5,
            "examples": [
                {"text": "пример " * 28, "translation": "перевод " * 37}
                for _ in range(5)
            ],
            "mini_dialogue": {
                "lines": [
                    {"text": "реплика " * 25, "translation": "перевод " * 37}
                    for _ in range(6)
                ]
            },
        }
    )
    rendered = format_explanation("Break a leg!", long_explanation)
    assert len(rendered) <= 3900
    assert rendered.endswith("…")


def test_short_explanation_is_not_touched() -> None:
    rendered = format_explanation("Break a leg!", make_explanation())
    assert "…" not in rendered.split("Мини-диалог")[-1] or len(rendered) < 3900
