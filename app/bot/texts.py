"""User-facing texts of the Telegram bot.

Kept in one module so wording can be reviewed in a single place and later
replaced with per-user localization without touching handler logic.
"""

START_GREETING = (
    "Привет! Это <b>Lexi Loop</b> — персональный тренажёр английского.\n"
    "\n"
    "Я буду присылать слова и выражения с переводом, транскрипцией, "
    "примерами и аудио, а затем — упражнения и повторения, чтобы новый "
    "материал закреплялся, а не забывался.\n"
    "\n"
    "Начнём с короткой настройки профиля: уровень языка и цели."
)

START_GREETING_WITH_REGISTRATION_WARNING = (
    "{greeting}\n"
    "\n"
    "⚠️ Не получилось сохранить профиль — сервис временно недоступен. "
    "Можно продолжить, мы попробуем ещё раз позже."
)

SETUP_TEASER = (
    "Здесь появится короткая настройка профиля: уровень английского, цель "
    "обучения и удобное время уроков.\n"
    "\n"
    "Этот этап в разработке — я сообщу, когда будет готов."
)

HELP_TEXT = (
    "<b>Lexi Loop</b> — тренажёр английского языка в Telegram.\n"
    "\n"
    "Что я умею сейчас:\n"
    "• /start — знакомство и начало настройки профиля\n"
    "• /help — эта справка\n"
    "\n"
    "Скоро: уроки со словами и выражениями, транскрипция, аудио, "
    "упражнения и интервальные повторения."
)

SETUP_BUTTON = "⚙️ Настроить профиль"

# Callback data prefix for the setup button. A single constant now, a
# namespace for future profile callbacks later.
CALLBACK_SETUP_PROFILE = "setup:profile"
