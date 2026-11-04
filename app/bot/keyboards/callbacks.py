"""Typed callback data factories shared by bot keyboards and handlers."""

from aiogram.filters.callback_data import CallbackData


class OnboardingCB(CallbackData, prefix="onb", sep="~"):
    """Callbacks of the onboarding flow.

    ``step`` carries the step the button was rendered for; handlers
    compare it with the current FSM state, so a press of an old button is
    detected instead of being applied to the wrong step. ``~`` separates
    fields because values (times, zone names) may contain ``:``.
    """

    action: str
    step: str = ""
    value: str = ""


class SettingsCB(CallbackData, prefix="set", sep="~"):
    """Callbacks of the settings editor (``set~action~field~value``)."""

    action: str
    field: str = ""
    value: str = ""


class MenuCB(CallbackData, prefix="menu", sep="~"):
    """Callbacks of the main menu."""

    action: str
