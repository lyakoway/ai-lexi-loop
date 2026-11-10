"""Tests for lesson card rendering and keyboards."""

from app.bot import texts
from app.bot.keyboards.lesson import LessonCB, lesson_card_keyboard
from app.bot.lesson_view import format_card
from app.db.models import Expression


def make_expression(**overrides: object) -> Expression:
    values: dict[str, object] = {
        "id": 1,
        "language": "en",
        "text": "Break a leg!",
        "translation": "Ни пуха ни пера!",
        "ipa": "breɪk ə lɛɡ",
        "phonetic_hint_ru": "брЭйк э лЭг",
        "example": "You have an exam? Break a leg!",
        "example_translation": "Завтра экзамен? Ни пуха ни пера!",
        "level": "B1",
        "topic": "everyday",
    }
    values.update(overrides)
    return Expression(**values)


def test_card_shows_all_six_components_and_disclaimer() -> None:
    card = format_card(make_expression(), index=0, total=3)
    assert "Break a leg!" in card
    assert "/breɪk ə lɛɡ/" in card
    assert "брЭйк э лЭг" in card
    assert "Ни пуха ни пера!" in card
    assert "You have an exam? Break a leg!" in card
    assert "Завтра экзамен? Ни пуха ни пера!" in card
    assert texts.PHONETIC_DISCLAIMER in card


def test_card_numbering_and_level_topic_badge() -> None:
    card = format_card(make_expression(), index=2, total=5)
    assert "Карточка 3 из 5" in card
    assert "B1 · 🏡 Повседневное" in card


def test_card_escapes_html() -> None:
    card = format_card(
        make_expression(text="A & B <tag>", example="x > y"),
        index=0,
        total=1,
    )
    assert "A &amp; B &lt;tag&gt;" in card
    assert "x &gt; y" in card
    assert "<tag>" not in card


def test_stress_is_marked_in_seed_hints() -> None:
    """Every seed hint carries exactly one capital-letter stress mark."""
    from app.db.seed import SEED_EXPRESSIONS

    for entry in SEED_EXPRESSIONS:
        capitals = [char for char in entry.phonetic_hint_ru if char.isupper()]
        assert capitals, f"hint without stress mark: {entry.phonetic_hint_ru}"


def test_lesson_callback_format() -> None:
    packed = LessonCB(action="next", token=42, index=3).pack()
    assert packed == "lssn~next~42~3"
    unpacked = LessonCB.unpack(packed)
    assert unpacked.action == "next"
    assert unpacked.token == 42
    assert unpacked.index == 3


def test_middle_card_has_next_and_finish() -> None:
    keyboard = lesson_card_keyboard(token=42, index=0, total=3, expression_id=42)
    callbacks = [
        button.callback_data for row in keyboard.inline_keyboard for button in row
    ]
    assert "lssn~next~42~0" in callbacks
    assert "lssn~finish~42~0" in callbacks


def test_last_card_has_finish_and_explain() -> None:
    """The last card drops «next» but keeps finishing and explaining."""
    keyboard = lesson_card_keyboard(token=42, index=2, total=3, expression_id=42)
    callbacks = [
        button.callback_data or "" for row in keyboard.inline_keyboard for button in row
    ]
    assert "lssn~finish~42~2" in callbacks
    assert "lssn~next~42~2" not in callbacks
    assert "tut~explain~42~2" in callbacks
