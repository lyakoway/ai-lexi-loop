"""Tests for onboarding and settings keyboards."""

import pytest

from app.bot.keyboards.callbacks import MenuCB, OnboardingCB, SettingsCB
from app.bot.keyboards.onboarding import (
    SKIP_DEFAULTS,
    STEP_ORDER,
    confirm_keyboard,
    onboarding_step_keyboard,
    options_for_step,
    step_values,
)
from app.bot.keyboards.settings import (
    EDITABLE_FIELDS,
    field_options_keyboard,
    settings_menu_keyboard,
    topics_edit_keyboard,
)


def test_every_step_has_options() -> None:
    for step in STEP_ORDER:
        options = options_for_step(step)
        assert options, f"step {step} has no options"
        for value, label in options:
            assert value and label


def test_options_for_unknown_step_raises() -> None:
    with pytest.raises(ValueError):
        options_for_step("definitely-not-a-step")


def test_step_values_match_options() -> None:
    assert step_values("level") == {"A1", "A2", "B1", "B2", "C1", "C2", "unknown"}


def test_callback_data_round_trip() -> None:
    packed = OnboardingCB(action="ans", step="level", value="B1").pack()
    assert packed == "onb~ans~level~B1"
    unpacked = OnboardingCB.unpack(packed)
    assert unpacked.action == "ans"
    assert unpacked.step == "level"
    assert unpacked.value == "B1"


def test_settings_callback_data_format() -> None:
    packed = SettingsCB(action="apply", field="level", value="A2").pack()
    assert packed == "set~apply~level~A2"
    assert MenuCB(action="menu").pack() == "menu~menu"


def test_topics_keyboard_marks_selected() -> None:
    draft = {"topics": ["work", "sport"]}
    keyboard = onboarding_step_keyboard("topics", draft)
    labels = {button.text for row in keyboard.inline_keyboard for button in row}
    assert any(label.startswith("✅") and "Работа" in label for label in labels)
    assert any(not label.startswith("✅") for label in labels)


def test_first_step_has_no_back_button() -> None:
    keyboard = onboarding_step_keyboard("native_language", {})
    texts = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "⬅️ Назад" not in texts
    assert "❌ Отмена" in texts


def test_navigation_row_depends_on_step() -> None:
    skippable = onboarding_step_keyboard("topics", {})
    skippable_texts = [
        button.text for row in skippable.inline_keyboard for button in row
    ]
    assert "Пропустить ➡️" in skippable_texts

    required = onboarding_step_keyboard("level", {})
    required_texts = [button.text for row in required.inline_keyboard for button in row]
    assert "Пропустить ➡️" not in required_texts
    assert "⬅️ Назад" in required_texts


def test_skip_defaults_only_for_optional_steps() -> None:
    assert set(SKIP_DEFAULTS) == {"topics", "preferred_accent"}
    for step in SKIP_DEFAULTS:
        assert step in STEP_ORDER


def test_timezone_step_has_other_option_button() -> None:
    keyboard = onboarding_step_keyboard("timezone", {})
    callbacks = [
        button.callback_data or "" for row in keyboard.inline_keyboard for button in row
    ]
    assert any(callback.startswith("onb~tzother~timezone") for callback in callbacks)


def test_confirm_keyboard_actions() -> None:
    keyboard = confirm_keyboard()
    callbacks = [
        button.callback_data or "" for row in keyboard.inline_keyboard for button in row
    ]
    assert any(callback.startswith("onb~confirm~confirm") for callback in callbacks)
    assert any("restart" in callback for callback in callbacks)
    assert any("cancel" in callback for callback in callbacks)


def test_settings_menu_covers_all_editable_fields() -> None:
    keyboard = settings_menu_keyboard()
    callbacks = [
        button.callback_data for row in keyboard.inline_keyboard for button in row
    ]
    for field in EDITABLE_FIELDS:
        assert SettingsCB(action="edit", field=field).pack() in callbacks


def test_field_options_keyboard_contains_back() -> None:
    keyboard = field_options_keyboard("level")
    last_row = keyboard.inline_keyboard[-1]
    assert last_row[0].callback_data == "set~menu~~"


def test_topics_edit_keyboard_marks() -> None:
    keyboard = topics_edit_keyboard(selected={"music"})
    texts = [b.text for row in keyboard.inline_keyboard for b in row]
    assert any(text.startswith("✅") and "Музыка" in text for text in texts)
