"""Unit tests for the level-placement Telegram flow (Stage 6.4.7).

The diagnostic runs inside onboarding: the "unknown level" option
generates a test via the (mocked) PlacementService, answers walk
question by question, the recommendation is confirmed into the draft.
No LLM, no network; the onboarding draft survives the whole detour.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.handlers import onboarding as onboarding_handlers
from app.bot.handlers import placement as placement_handlers
from app.bot.handlers.onboarding import OnboardingFlow
from app.bot.handlers.placement import PlacementFlow
from app.bot.keyboards.callbacks import OnboardingCB
from app.bot.keyboards.placement import PlacementCB
from app.services import PlacementService
from app.services.placement import (
    PlacementQuestion,
    PlacementSet,
    PlacementUnavailable,
)

TEST_TELEGRAM_ID = 12345

QUESTION_A2 = PlacementQuestion(
    text="She ___ to school every day.",
    options=("go", "goes", "going", "gone"),
    level="A2",
    correct_index=1,
)
QUESTION_B1 = PlacementQuestion(
    text="If I ___ more time, I would travel.",
    options=("have", "had", "will have", "having"),
    level="B1",
    correct_index=1,
)


def make_state() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=TEST_TELEGRAM_ID)
    return FSMContext(storage=storage, key=key)


def make_callback() -> Any:
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.from_user = SimpleNamespace(id=TEST_TELEGRAM_ID)
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    return callback


def make_service() -> Any:
    return AsyncMock(spec=PlacementService)


def make_placement_set() -> PlacementSet:
    return PlacementSet(questions=(QUESTION_A2, QUESTION_B1))


async def enter_level_step(state: FSMContext) -> None:
    """Put the FSM into the onboarding level step with a language draft."""
    await state.set_data({"native_language": "ru", "target_language": "en"})
    await state.set_state(OnboardingFlow.level)


async def test_unknown_level_starts_diagnostic() -> None:
    state = make_state()
    await enter_level_step(state)
    callback = make_callback()
    service = make_service()
    service.generate.return_value = make_placement_set()

    await onboarding_handlers.on_answer(
        callback,
        OnboardingCB(action="ans", step="level", value="unknown"),
        state,
        service,
    )

    service.generate.assert_awaited_once_with(
        target_language="en", native_language="ru"
    )
    assert await state.get_state() == PlacementFlow.question.state
    rendered = callback.message.edit_text.await_args.args[0]
    assert "Вопрос 1 из 2" in rendered
    keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
    option_labels = {button.text for row in keyboard.inline_keyboard for button in row}
    assert {"go", "goes", "going", "gone"} <= option_labels
    data = await state.get_data()
    assert len(data["placement_questions"]) == 2
    # The draft fields survive.
    assert data["target_language"] == "en"


async def test_diagnostic_unavailable_falls_back_to_manual() -> None:
    state = make_state()
    await enter_level_step(state)
    callback = make_callback()
    service = make_service()
    service.generate.return_value = PlacementUnavailable(reason="llm_disabled")

    await onboarding_handlers.on_answer(
        callback,
        OnboardingCB(action="ans", step="level", value="unknown"),
        state,
        service,
    )

    callback.answer.assert_any_await(texts.PLACEMENT_UNAVAILABLE, show_alert=True)
    assert await state.get_state() == OnboardingFlow.level.state


async def test_diagnostic_invalid_output_falls_back_to_manual() -> None:
    state = make_state()
    await enter_level_step(state)
    callback = make_callback()
    service = make_service()
    service.generate.return_value = PlacementUnavailable(reason="invalid_output")

    await onboarding_handlers.on_answer(
        callback,
        OnboardingCB(action="ans", step="level", value="unknown"),
        state,
        service,
    )

    assert await state.get_state() == OnboardingFlow.level.state


async def test_regular_level_choice_still_stored_directly() -> None:
    state = make_state()
    await enter_level_step(state)
    callback = make_callback()
    service = make_service()

    await onboarding_handlers.on_answer(
        callback, OnboardingCB(action="ans", step="level", value="B1"), state, service
    )

    service.generate.assert_not_awaited()
    assert (await state.get_data())["level"] == "B1"
    assert await state.get_state() == OnboardingFlow.learning_goal.state


# ---------------------------------------------------------------------------
# Answering the questions
# ---------------------------------------------------------------------------


async def enter_diagnostic(state: FSMContext, service: Any) -> None:
    await enter_level_step(state)
    service.generate.return_value = make_placement_set()
    callback = make_callback()
    await onboarding_handlers.on_answer(
        callback,
        OnboardingCB(action="ans", step="level", value="unknown"),
        state,
        service,
    )


async def test_answers_lead_to_recommendation() -> None:
    state = make_state()
    service = make_service()
    await enter_diagnostic(state, service)

    # Both questions answered correctly (option index 1).
    await placement_handlers.on_placement_answer(
        make_callback(), PlacementCB(action="ans", index=0, value=1), state
    )
    await placement_handlers.on_placement_answer(
        make_callback(), PlacementCB(action="ans", index=1, value=1), state
    )

    assert await state.get_state() == PlacementFlow.done.state
    data = await state.get_data()
    assert data["placement_answers"] == [1, 1]


async def test_stale_answer_index_is_rejected() -> None:
    state = make_state()
    service = make_service()
    await enter_diagnostic(state, service)

    callback = make_callback()
    await placement_handlers.on_placement_answer(
        callback, PlacementCB(action="ans", index=1, value=1), state
    )

    callback.answer.assert_awaited_once()
    assert "неактивна" in callback.answer.await_args.args[0]
    data = await state.get_data()
    assert data["placement_answers"] == []


async def test_confirm_writes_level_and_continues_onboarding() -> None:
    state = make_state()
    service = make_service()
    await enter_diagnostic(state, service)
    await placement_handlers.on_placement_answer(
        make_callback(), PlacementCB(action="ans", index=0, value=1), state
    )
    await placement_handlers.on_placement_answer(
        make_callback(), PlacementCB(action="ans", index=1, value=1), state
    )

    callback = make_callback()
    await placement_handlers.on_placement_confirm(callback, state)

    data = await state.get_data()
    assert data["level"] == "B1"  # both A2 and B1 bands passed
    assert await state.get_state() == OnboardingFlow.learning_goal.state
    # The diagnostic payload is cleared from the draft.
    assert data["placement_questions"] is None
    rendered = callback.message.edit_text.await_args.args[0]
    assert "Зачем вам" in rendered  # the learning_goal step follows


async def test_manual_choice_returns_to_level_step() -> None:
    state = make_state()
    service = make_service()
    await enter_diagnostic(state, service)
    await placement_handlers.on_placement_answer(
        make_callback(), PlacementCB(action="ans", index=0, value=1), state
    )
    await placement_handlers.on_placement_answer(
        make_callback(), PlacementCB(action="ans", index=1, value=1), state
    )

    callback = make_callback()
    await placement_handlers.on_placement_manual(callback, state)

    assert await state.get_state() == OnboardingFlow.level.state
    data = await state.get_data()
    assert data["placement_questions"] is None
    assert "level" not in data  # nothing was written into the draft


async def test_wrong_answers_recommend_lower_level() -> None:
    state = make_state()
    service = make_service()
    await enter_diagnostic(state, service)
    await placement_handlers.on_placement_answer(
        make_callback(), PlacementCB(action="ans", index=0, value=0), state
    )
    await placement_handlers.on_placement_answer(
        make_callback(), PlacementCB(action="ans", index=1, value=0), state
    )

    callback = make_callback()
    await placement_handlers.on_placement_confirm(callback, state)

    assert (await state.get_data())["level"] == "A1"


async def test_callback_round_trip() -> None:
    packed = PlacementCB(action="ans", index=2, value=3).pack()
    unpacked = PlacementCB.unpack(packed)
    assert (unpacked.action, unpacked.index, unpacked.value) == ("ans", 2, 3)
