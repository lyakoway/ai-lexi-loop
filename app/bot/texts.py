"""User-facing texts of the Telegram bot.

Kept in one module so wording can be reviewed in a single place and later
replaced with per-user localization without touching handler logic.
"""

from app.domain.languages import supported_target_languages

# ---------------------------------------------------------------------------
# Commands and menus
# ---------------------------------------------------------------------------

START_GREETING_NEW = (
    "Привет! Это <b>Lexi Loop</b> — персональный тренажёр английского.\n"
    "\n"
    "Я буду присылать слова и выражения с переводом, транскрипцией, "
    "примерами и аудио, а затем — упражнения и повторения, чтобы новый "
    "материал закреплялся, а не забывался.\n"
    "\n"
    "Сначала короткая настройка: уровень, цель, темы и расписание. "
    "Это займёт пару минут."
)

START_GREETING_EXISTING = (
    "С возвращением! 👋\n"
    "\n"
    "Профиль настроен — вы готовы к занятиям. Загляните в профиль или "
    "поменяйте настройки в любой момент."
)

START_GREETING_WITH_REGISTRATION_WARNING = (
    "{greeting}\n"
    "\n"
    "⚠️ Не получилось сохранить профиль — сервис временно недоступен. "
    "Можно продолжить, мы попробуем ещё раз позже."
)

MAIN_MENU_TEXT = (
    "<b>Lexi Loop</b> — тренажёр английского языка.\n"
    "\n"
    "• /lesson — урок с новыми выражениями\n"
    "• /review — повторение изученного\n"
    "• /stats — статистика обучения\n"
    "• /token — токен для Web API\n"
    "• /profile — ваш профиль\n"
    "• /settings — настройки обучения\n"
    "• /help — справка"
)

HELP_TEXT = (
    "<b>Lexi Loop</b> — тренажёр английского языка в Telegram.\n"
    "\n"
    "Команды:\n"
    "• /start — знакомство и главное меню\n"
    "• /lesson — урок с новыми выражениями\n"
    "• /review — повторение изученного\n"
    "• /stats — статистика обучения\n"
    "• /token — токен для Web API\n"
    "• /profile — текущий профиль обучения\n"
    "• /settings — изменить отдельные настройки\n"
    "• /cancel — отменить текущее действие\n"
    "• /help — эта справка\n"
    "\n"
    "Скоро: аудио, упражнения и интервальные повторения."
)

PROFILE_TITLE = "<b>👤 Профиль обучения</b>"
PROFILE_EMPTY = (
    "Профиль ещё не настроен.\n"
    "\n"
    "Пройдите короткую настройку — это займёт пару минут."
)
PROFILE_EMPTY_FOR_SETTINGS = (
    "Профиль ещё не настроен, менять пока нечего.\n" "\n" "Сначала пройдите настройку."
)

SETTINGS_MENU_TITLE = "<b>⚙️ Настройки обучения</b>\n"
SETTINGS_FIELD_SAVED = "Сохранено ✅"

ONBOARDING_CANCELLED = "Настройка отменена. Сохранённые ранее данные не изменялись."
ACTION_CANCELLED = "Действие отменено. Уже сохранённые данные не изменились."
ONBOARDING_SAVED = (
    "Профиль сохранён ✅\n"
    "\n"
    "Уроки подстроятся под эти настройки. Изменить их можно командой "
    "/settings."
)
ONBOARDING_STALE = (
    "Эта настройка уже неактивна. Откройте /settings, чтобы начать заново."
)
ONBOARDING_CANT_SKIP = "Этот шаг нельзя пропустить — выберите вариант."
ONBOARDING_BACK_UNAVAILABLE = "Это первый шаг — назад некуда."
ONBOARDING_CONFIRM_TITLE = "<b>Проверьте настройки</b>\n"
ONBOARDING_CONFIRM_PROMPT = "\nВсё верно?"
ONBOARDING_RESTARTED = "Начинаем настройку заново."
ONBOARDING_INVALID_ANSWER = (
    "Кнопка не подходит к текущему шагу — возможно, вы нажали старую. "
    "Продолжайте с текущего вопроса."
)
TIMEZONE_INPUT_PROMPT = (
    "Напишите город или часовой пояс текстом, например: <i>Москва</i>, "
    "<i>новосибирск</i>, <i>Europe/Berlin</i>."
)
TIMEZONE_NOT_FOUND = (
    "Не смог распознать «{query}». Попробуйте ещё раз: например, "
    "<i>Москва</i> или <i>Europe/Riga</i>."
)

# ---------------------------------------------------------------------------
# Onboarding step prompts (keys match ProfileCreate field names)
# ---------------------------------------------------------------------------

ONBOARDING_PROMPTS: dict[str, str] = {
    "native_language": "1/11. На каком языке вам удобно заниматься? "
    "Интерфейс и объяснения будут на нём.",
    "target_language": "2/11. Какой язык изучаем?",
    "level": (
        "3/11. Какой у вас уровень английского?\n"
        "\n"
        "Если не уверены — выберите «Не знаю», подберём по ходу."
    ),
    "learning_goal": "4/11. Зачем вам английский?",
    "topics": (
        "5/11. Какие темы вам интересны?\n"
        "\n"
        "Можно выбрать несколько или пропустить."
    ),
    "preferred_accent": ("6/11. Какой вариант произношения предпочитаете?"),
    "daily_study_minutes": "7/11. Сколько времени готовы заниматься в день?",
    "new_items_per_day": ("8/11. Сколько новых слов и выражений показывать в день?"),
    "lesson_time": ("9/11. Во сколько присылать ежедневный урок?"),
    "review_time": ("10/11. Во сколько присылать повторение выученного?"),
    "timezone": ("11/11. Ваш часовой пояс — по нему я рассчитаю время уроков."),
}

# ---------------------------------------------------------------------------
# Value labels
# ---------------------------------------------------------------------------

LANGUAGE_LABELS: dict[str, str] = {
    "ru": "🇷🇺 Русский",
    # Target languages come from the language profile registry: adding a
    # language there automatically extends every keyboard and profile view.
    **{profile.code: profile.label for profile in supported_target_languages()},
}

LEVEL_BUTTON_LABELS: dict[str, str] = {
    "A1": "A1",
    "A2": "A2",
    "B1": "B1",
    "B2": "B2",
    "C1": "C1",
    "C2": "C2",
}

LEVEL_VIEW_LABELS: dict[str, str] = {
    "unknown": "определим по ходу",
    "A1": "A1 — начинаю с нуля",
    "A2": "A2 — базовый",
    "B1": "B1 — средний",
    "B2": "B2 — выше среднего",
    "C1": "C1 — продвинутый",
    "C2": "C2 — почти носитель",
}

GOAL_LABELS: dict[str, str] = {
    "work": "💼 Работа",
    "travel": "✈️ Путешествия",
    "exams": "🎓 Экзамены",
    "relocation": "🚚 Переезд",
    "media": "🎬 Фильмы и музыка",
    "for_myself": "🌱 Для себя",
}

TOPIC_LABELS: dict[str, str] = {
    "everyday": "🏡 Повседневное",
    "work": "💼 Работа",
    "travel": "✈️ Путешествия",
    "technology": "💻 Технологии",
    "business": "📊 Бизнес",
    "science": "🔬 Наука",
    "culture": "🎭 Культура",
    "sport": "⚽ Спорт",
    "movies": "🎬 Кино",
    "music": "🎵 Музыка",
    "games": "🎮 Игры",
}

ACCENT_LABELS: dict[str, str] = {
    "us": "🇺🇸 Американский",
    "uk": "🇬🇧 Британский",
}

MINUTES_LABELS: dict[str, str] = {
    "5": "5 минут",
    "10": "10 минут",
    "15": "15 минут",
    "30": "30 минут",
    "45": "45 минут",
    "60": "1 час",
    "90": "полтора часа",
}

FIELD_LABELS: dict[str, str] = {
    "native_language": "Родной язык",
    "target_language": "Изучаемый язык",
    "level": "Уровень",
    "learning_goal": "Цель",
    "topics": "Темы",
    "preferred_accent": "Акцент",
    "daily_study_minutes": "Время в день",
    "new_items_per_day": "Новых слов в день",
    "lesson_time": "Время урока",
    "review_time": "Время повторения",
    "timezone": "Часовой пояс",
}

# Button captions
BTN_START_SETUP = "⚙️ Настроить профиль"
BTN_OPEN_PROFILE = "👤 Мой профиль"
BTN_OPEN_SETTINGS = "⚙️ Настройки"
BTN_OPEN_HELP = "ℹ️ Как это работает"
BTN_BACK = "⬅️ Назад"
BTN_SKIP = "Пропустить ➡️"
BTN_CANCEL = "❌ Отмена"
BTN_TOPICS_DONE = "✅ Готово"
BTN_CONFIRM = "✅ Всё верно, сохранить"
BTN_ONBOARDING_RESTART = "🔄 Начать заново"
BTN_TIMEZONE_OTHER = "✍️ Другой вариант"
BTN_TO_MENU = "⬅️ В меню"
BTN_RERUN_ONBOARDING = "🔄 Пройти настройку заново"

NO_TOPICS_SELECTED = "не выбраны"

# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------

BTN_OPEN_LESSON = "📚 Урок"
BTN_NEXT_CARD = "➡️ Следующее"
BTN_FINISH_LESSON = "✅ Завершить урок"

LESSON_DEFAULT_LEVEL_NOTE = (
    "ℹ️ Уровень не выбран — начинаем с A2 и подстроимся по ходу. " "Изменить: /settings"
)
LESSON_STALE = "Этот урок уже неактивен. Начните новый: /lesson"
LESSON_BUILD_FAILED = (
    "Не удалось собрать урок — сервис временно недоступен. "
    "Попробуйте ещё раз чуть позже."
)
LESSON_SAVE_FAILED = (
    "Не удалось сохранить прогресс — попробуйте ещё раз нажать " "«Завершить урок»."
)
LESSON_FINISHED = (
    "Урок завершён ✅ Пройдено карточек: {count}.\n\n"
    "Новые выражения — по команде /lesson."
)
LESSON_UNAVAILABLE_MESSAGES = {
    "no_expressions_for_topics": (
        "По вашим темам в словаре пока нет выражений — он пока небольшой "
        "и постоянно растёт.\n\n"
        "Можно выбрать другие темы в /settings или заглянуть позже."
    ),
    "no_expressions_for_level": (
        "Для уровня {level} по выбранным темам выражений пока нет — "
        "словарь пока небольшой.\n\n"
        "Попробуйте изменить уровень или темы в /settings."
    ),
    "all_served": (
        "Новые выражения на сегодня закончились 🎉\n\n"
        "Интервальные повторения появятся на следующем этапе. "
        "Настройки можно изменить в /settings."
    ),
}
PHONETIC_DISCLAIMER = (
    "⚠️ Подсказка русскими буквами приблизительна и не передаёт "
    "все английские звуки."
)

# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

BTN_OPEN_REVIEW = "🔁 Повторение"
BTN_REMEMBER = "✅ Помню"
BTN_FORGOT = "🔁 Забыл"

REVIEW_PREFIX = "🔁 <b>Повторение</b>"
REVIEW_NO_DUE = (
    "Повторений пока нет 🙌\n\n"
    "Карточки становятся доступны к повторению через день после урока. "
    "Загляните позже или пройдите новый урок: /lesson."
)
REVIEW_FINISHED = (
    "Повторение завершено ✅\n"
    "Помню: {remembered} · Забыл: {forgot}\n\n"
    "Следующие повторения рассчитаны по вашим ответам."
)
REVIEW_STALE = "Это повторение уже неактивно. Начните новое: /review"
REVIEW_ANSWER_FAILED = "Не удалось сохранить ответ — попробуйте ещё раз."
REVIEW_BUILD_FAILED = (
    "Не удалось открыть повторение — сервис временно недоступен. "
    "Попробуйте ещё раз чуть позже."
)

# ---------------------------------------------------------------------------
# Stats and API token
# ---------------------------------------------------------------------------

STATS_TITLE = "<b>📊 Ваша статистика</b>"
STATS_LESSONS_COMPLETED = "Завершено уроков (с запуска учёта): {value}"
STATS_TOKEN_HINT = (
    "Токен для Web API отправлен выше один раз. Если потеряли — выпустите "
    "новый командой /token (старые продолжат работать)."
)
TOKEN_TITLE = "<b>🔑 Токен для Web API</b>"
TOKEN_VALUE = "<code>{token}</code>"
TOKEN_INSTRUCTIONS = (
    "Используйте его в заголовке Authorization: Bearer … при запросах к "
    "/api/v1. Токен показывается только один раз — сохраните его. "
    "Никому не передавайте: токен даёт доступ к вашим данным."
)
TOKEN_LIMIT_REACHED = (
    "У вас уже выпущено максимум токенов ({limit}). Новые появятся "
    "вместе с управлением токенами в Web-панели."
)
TOKEN_ISSUE_FAILED = "Не удалось выпустить токен — попробуйте ещё раз чуть позже."

# ---------------------------------------------------------------------------
# AI tutor (explain expression)
# ---------------------------------------------------------------------------

BTN_EXPLAIN = "🧠 Объяснить"
BTN_BACK_TO_CARD = "⬅️ К карточке"
TUTOR_LOADING = "Готовлю объяснение…"
TUTOR_STALE = (
    "Это объяснение уже неактуально — карточка изменилась. "
    "Нажмите «Объяснить» на текущей карточке."
)
TUTOR_UNAVAILABLE = (
    "Не удалось получить объяснение — попробуйте позже. "
    "Обучение можно продолжить без него."
)
TUTOR_AI_NOTE = "🤖 Объяснение сгенерировано AI и может содержать неточности."

# ---------------------------------------------------------------------------
# AI generation (/generate)
# ---------------------------------------------------------------------------

CMD_GENERATE_HINT = (
    "Генерирую персональные выражения по вашей теме и уровню. "
    "После просмотра вы решаете, добавить ли их в обучение."
)
BTN_OPEN_GENERATE = "🎲 Сгенерировать выражения"
GEN_CHOOSE_TOPIC = "1/3. Выберите тему:"
GEN_ANY_TOPIC = "🎲 Любая тема"
GEN_CHOOSE_COUNT = "2/3. Сколько выражений сгенерировать?"
GEN_COUNT_CHOICES = ("3", "5", "7")
GEN_CHOOSE_SITUATION = (
    "3/3. Опишите ситуацию или цель (необязательно) — или нажмите " "«Пропустить»."
)
GEN_SITUATION_HINT = (
    "Например: разговор с коллегой перед дедлайном, заказ кофе в "
    "кофейне, маленький разговор с соседом."
)
GEN_SKIP = "Пропустить ➡️"
GEN_GENERATING = "🎲 Генерирую выражения…"
GEN_PREVIEW_TITLE = (
    "<b>🎲 Персональные выражения</b>\n"
    "<i>Черновик AI — просмотрите перед добавлением.</i>"
)
GEN_PREVIEW_ITEM = "{number}. <b>{text}</b> — {translation}"
GEN_NONE_SAVED = (
    "Все предложенные выражения уже были у вас в обучении. " "Попробуйте другую тему."
)
GEN_ACCEPT = "✅ Добавить в обучение"
GEN_REJECT = "🗑 Не добавлять"
GEN_ACCEPTED_DONE = (
    "Готово ✅ Добавлено в обучение: {accepted}.\n"
    "Первое повторение — завтра, дальше по алгоритму повторений."
)
GEN_REJECTED_DONE = "Черновики удалены из рассмотрения ✅"
GEN_STALE = "Этот набор уже неактивен. Начните заново: /generate"
GEN_UNAVAILABLE = (
    "Не удалось сгенерировать выражения — попробуйте позже. "
    "Обучение продолжается без изменений."
)

# ---------------------------------------------------------------------------
# AI Tutor Chat (/chat, /close, /history)
# ---------------------------------------------------------------------------

BTN_OPEN_CHAT = "💬 Практика с тьютором"
BTN_LISTEN = "🔊 Послушать"
CHAT_STARTED = (
    "💬 Режим практики включён.\n"
    "Пишите сообщения на изучаемом языке — я отвечу, мягко поправлю "
    "ошибки и подскажу естественные формулировки.\n\n"
    "Тема: {topic}. /close — завершить, /history — последние сообщения."
)
CHAT_RESUMED = "💬 Продолжаем диалог ({count} сообщений). Пишите!"
CHAT_REPLY_PREFIX = "🤖"
CHAT_CORRECTION_TITLE = "🛠 <b>Как точнее:</b>"
CHAT_CORRECTION_NOTE_TITLE = "📝"
CHAT_ALTERNATIVE_TITLE = "💡 <b>Естественнее:</b>"
CHAT_FOLLOWUP_TITLE = "❓"
CHAT_NON_TEXT = "Напишите сообщение текстом 🙂"
CHAT_MESSAGE_TOO_LONG = "Сообщение слишком длинное (до 1000 символов) — разделите его."
CHAT_BUSY = "Тьютор отвечает на предыдущее сообщение — секундочку и повторите."
CHAT_RATE_LIMITED = "Слишком часто — подождите пару секунд."
CHAT_CLAIM_LOST = "Не сохранилось — отправьте сообщение ещё раз."
CHAT_UNAVAILABLE = (
    "AI тьютор временно недоступен — попробуйте позже. "
    "Обучение продолжается: /lesson, /review."
)
CHAT_CLOSED = "💬 Диалог закрыт. /chat — начать новый."
CHAT_NOT_FOUND = "Активного диалога нет — начните: /chat"
CHAT_HISTORY_EMPTY = "Пока нет сообщений — напишите первое в /chat."
CHAT_HISTORY_TITLE = "<b>💬 Последние сообщения</b>"

# Map of ChatService reason -> user-facing text (no internal codes).
# The chat handler renders strictly from this dict: if a reason is not
# here, the user gets the generic "unavailable" text — and the
# parametrized test fails on any reason leaking into the answer.
CHAT_ERROR_TEXTS: dict[str, str] = {
    "busy": CHAT_BUSY,
    "rate_limited": CHAT_RATE_LIMITED,
    "claim_lost": CHAT_CLAIM_LOST,
    "message_too_long": CHAT_MESSAGE_TOO_LONG,
    "conversation_closed": CHAT_CLOSED,
    "conversation_not_found": CHAT_NOT_FOUND,
    "profile_not_found": CHAT_NOT_FOUND,
    "llm_disabled": CHAT_UNAVAILABLE,
    "llm_error": CHAT_UNAVAILABLE,
    "invalid_output": CHAT_UNAVAILABLE,
    "idempotency_conflict": CHAT_BUSY,
    "idempotency_pending": CHAT_CLAIM_LOST,
}

# ---------------------------------------------------------------------------
# TTS playback (Stage 6.4.5)
# ---------------------------------------------------------------------------

TTS_UNAVAILABLE = "Озвучка временно недоступна."
TTS_VOICE_CAPTION = "🔊 {text}"

# ---------------------------------------------------------------------------
# Active recall quiz (Stage 6.4.6)
# ---------------------------------------------------------------------------

BTN_OPEN_QUIZ = "🎯 Квиз"
QUIZ_PREFIX = "🎯 <b>Квиз</b>"
QUIZ_QUESTION = (
    QUIZ_PREFIX + "\n\nКак будет по-английски:\n«{question}»?\n\n"
    "Напишите ответ свободной формой — смысл важнее точной формулировки. "
    "/quiz — другой вопрос."
)
QUIZ_CORRECT = "✅ <b>Верно!</b>"
QUIZ_WRONG = "❌ <b>Не совсем.</b>"
QUIZ_CORRECTED_TITLE = "Правильный ответ:"
QUIZ_EXPLANATION_TITLE = "💬 {explanation}"
QUIZ_NEXT_REVIEW = "⏰ Следующее повторение: {date}"
QUIZ_EVALUATION_RETRY = (
    "Не смог оценить ответ — попробуйте сформулировать его ещё раз. "
    "Прогресс не пострадал."
)
QUIZ_UNAVAILABLE = (
    "Квиз временно недоступен — попробуйте позже. "
    "Обучение продолжается: /lesson, /review."
)
QUIZ_NO_WORDS = (
    "Пока нечего квизить: сначала пройдите урок — /lesson. "
    "Квиз собирается из слов, которые уже были в вашем обучении."
)
QUIZ_ANSWER_TOO_LONG = "Ответ слишком длинный (до 500 символов) — сократите его."
QUIZ_STALE = "Этот вопрос уже неактивен. Начните заново: /quiz"
QUIZ_EXITED = "Квиз завершён. /quiz — новый вопрос."
QUIZ_NON_TEXT = "Напишите ответ текстом 🙂"
QUIZ_LEVEL_OFFER = (
    "\n\n🚀 Устойчивая серия: {streak} верных ответов подряд! "
    "Готовы к следующему уровню?"
)
QUIZ_LEVEL_UP_BUTTON = "🚀 Готов к следующему уровню"
QUIZ_LEVEL_UP_DONE = (
    "🎉 Уровень повышен: {level}. Новые карточки будут соответствовать ему."
)
QUIZ_LEVEL_UP_DECLINED = (
    "Уровень пока не повышен — серия верных ответов прервалась или уже "
    "использована. Продолжайте квиз: /quiz."
)
QUIZ_LEVEL_UP_TOP = "Вы уже на максимальном уровне C2 — дальше только практика 💪"
FORGOTTEN_TITLE = "😵 <b>Топ забытого</b>"
FORGOTTEN_ITEM = "{number}. <b>{text}</b> — {translation} (провалов: {lapses})"
FORGOTTEN_EMPTY = (
    "Забытых слов нет 🙌 Все карточки на месте. Возвращайтесь через неделю."
)
FORGOTTEN_THROTTLED = (
    "Отчёт «Топ забытого» можно смотреть раз в неделю. "
    "Следующий раз — {date}. А пока: /quiz, /review."
)

# Map of QuizService reason -> user-facing text (no internal codes), the
# same contract as CHAT_ERROR_TEXTS: the handler renders strictly from
# this dict and the parametrized test fails on any reason leaking into
# the answer.
QUIZ_ERROR_TEXTS: dict[str, str] = {
    "no_words": QUIZ_NO_WORDS,
    "expression_not_found": QUIZ_STALE,
    "answer_too_long": QUIZ_ANSWER_TOO_LONG,
    "llm_disabled": QUIZ_UNAVAILABLE,
    "llm_error": QUIZ_UNAVAILABLE,
    "invalid_output": QUIZ_EVALUATION_RETRY,
    "profile_not_found": QUIZ_NO_WORDS,
}

# ---------------------------------------------------------------------------
# Level placement diagnostic (Stage 6.4.7)
# ---------------------------------------------------------------------------

PLACEMENT_RUNNING = "🔍 Готовлю диагностику уровня…"
PLACEMENT_UNAVAILABLE = "Диагностика временно недоступна — выберите уровень вручную."
PLACEMENT_QUESTION = "Вопрос {number} из {total}\n\n{question}"
PLACEMENT_RECOMMENDED = (
    "Диагностика завершена 🎉\n\nПо вашим ответам рекомендуемый уровень — "
    "<b>{level}</b>.\nЕго можно подтвердить или выбрать вручную."
)
PLACEMENT_STALE = "Диагностика уже неактивна — вернитесь к выбору уровня."
BTN_CONFIRM_LEVEL = "✅ Подтвердить {level}"
BTN_PICK_MANUAL = "✍️ Выбрать уровень вручную"
