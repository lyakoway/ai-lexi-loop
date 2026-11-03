# ai-lexi-loop

Персональный AI-тренажёр английского языка с доставкой уроков через Telegram.
Цель продукта — помочь пользователю быстрее начать понимать и самостоятельно
использовать английский: слова и выражения, перевод, IPA-транскрипция,
приблизительное произношение русскими буквами, примеры и аудио, позже —
упражнения и интервальные повторения.

Этот репозиторий — **этап 1: инженерный фундамент**. Генерация уроков через
LLM, TTS, профиль, повторения и планировщик здесь намеренно отсутствуют;
архитектура рассчитана на их постепенное добавление.

## Что уже есть

- FastAPI с health-эндпоинтами: `GET /health` (liveness) и `GET /ready`
  (readiness: проверка БД и статуса Telegram-polling).
- Telegram-бот на aiogram 3: `/start` (приветствие + кнопка настройки
  профиля + регистрация пользователя) и `/help`.
- PostgreSQL через SQLAlchemy 2 (async) + Alembic-миграция таблицы `users`.
- Слоистая архитектура: handlers → services → repositories → db.
- pytest (unit + integration на реальном PostgreSQL), Ruff, Black, mypy
  (strict), pre-commit, GitHub Actions CI, Docker Compose.

## Архитектура

Модульный монолит с явными границами слоёв:

```
app/
├── api/            FastAPI: фабрика приложения, роуты, обработка ошибок
├── bot/            Telegram: handlers, keyboards, middlewares, polling-раннер
├── core/           Конфигурация (pydantic-settings) и логирование
├── db/             Engine/сессии SQLAlchemy, ORM-модели
├── repositories/   Доступ к данным (SQL), перевод ошибок БД в StorageError
├── services/       Бизнес-логика (сейчас: регистрация пользователей)
└── main.py         Точка входа: python -m app.main
```

Ключевые решения:

- **Один процесс — два компонента.** FastAPI и Telegram polling запускаются
  вместе (`app/api/app.py` lifespan). Polling работает в отдельной
  наблюдаемой задаче (`app/bot/runner.py`): если он падает, `/ready`
  начинает отвечать 503, процесс не «притворяется» здоровым.
- **Handlers без бизнес-логики.** Обработчики бота только переводят
  обновления Telegram в вызовы сервисов. Сессия БД и сервисы попадают в
  обработчики через DI-middleware (`app/bot/middlewares/di.py`).
- **Никаких глобальных сессий.** Engine создаётся в lifespan приложения и
  передаётся явно; тесты полностью управляют зависимостями.
- **Миграции отдельно от приложения.** Приложение никогда не вызывает
  `create_all()`; схема создаётся командой `alembic upgrade head`
  (в Docker Compose — отдельный one-shot сервис `migrate`, поэтому
  несколько реплик приложения не применяют миграции одновременно).

Каталоги `domain/` и `integrations/` (LLM, TTS, словари) из целевой
структуры пока не созданы: реальных модулей для них на этом этапе нет.
Появятся вместе с первой интеграцией.

## Быстрый старт (локально, без Docker)

Требуются: Python 3.12+, [uv](https://docs.astral.sh/uv/), PostgreSQL.

```bash
cp .env.example .env          # заполните DATABASE_URL и (опционально) токен бота
uv sync                       # создать .venv и установить зависимости из uv.lock
uv run alembic upgrade head   # применить миграции
uv run python -m app.main     # поднять API + бота
```

Проверка: `curl http://localhost:8000/health` и `curl http://localhost:8000/ready`.

Без `TELEGRAM_BOT_TOKEN` бот не запускается (`/ready` вернёт `"bot":
"disabled"`) — удобно для локальной разработки. В `ENVIRONMENT=production`
отсутствующий токен — ошибка старта.

## Быстрый старт (Docker Compose)

```bash
cp .env.example .env
docker compose up --build
```

Поднимаются PostgreSQL (с healthcheck и persistent volume), one-shot
сервис `migrate` (применяет миграции до старта приложения) и `app`.

## Переменные окружения

| Переменная           | Обязательна        | Описание                                     |
| -------------------- | ------------------ | -------------------------------------------- |
| `DATABASE_URL`       | да                 | строка подключения PostgreSQL                |
| `TELEGRAM_BOT_TOKEN` | в production       | токен из @BotFather                           |
| `ENVIRONMENT`        | нет (`dev`)        | `dev` / `staging` / `production`             |
| `HOST`, `PORT`       | нет (`0.0.0.0:8000`) | параметры HTTP-сервера                     |
| `LOG_LEVEL`          | нет (`INFO`)       | уровень логирования                          |
| `TESTING`            | нет (`false`)      | тестовый режим: отключает Telegram-polling   |

Секреты хранятся только в `.env` (в Git не попадает) или переменных
окружения; в настройках токен — `SecretStr`, в логи не пишется.

## Проверки качества

```bash
uv run black .             # форматирование
uv run black --check .     # проверка форматирования без изменений
uv run ruff check .        # линтер
uv run mypy .              # статическая типизация (strict)
uv run pytest              # тесты
./scripts/check_all.sh     # всё перечисленное одной командой
```

### Тесты

- `tests/unit/` — без сети и БД: настройки, фабрика приложения, `/health`,
  `/ready` (недоступная БД), обработчики `/start` и `/help`, lifecycle
  сессий, раннер polling, обработка ошибок.
- `tests/integration/` — реальный PostgreSQL: применение Alembic-миграций,
  уникальность `telegram_user_id`, репозиторий, `/ready` с живой БД.
  Запускаются только при установленной переменной `TEST_DATABASE_URL`,
  иначе пропускаются с явной причиной.

Пример запуска интеграционных тестов:

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ai_lexi_loop_test \
  uv run pytest
```

В CI (`.github/workflows/ci.yml`) PostgreSQL поднимается как service
container, поэтому интеграционные тесты выполняются всегда.

### pre-commit

```bash
uv run pre-commit install   # один раз
```

Перед каждым коммитом: Ruff, Black, mypy (из lock-файла проекта) и
базовые проверки файлов.

## Миграции

```bash
uv run alembic upgrade head        # применить
uv run alembic downgrade base      # откатить
uv run alembic revision -m "..."   # создать новую (autogenerate доступен)
```

URL базы данных берётся из настроек приложения (`DATABASE_URL`), в
`alembic.ini` он не хранится.

## Статус этапа и ограничения

- Docker на машине разработки отсутствовал — `Dockerfile` и
  `docker-compose.yml` написаны, но сборка образа не проверялась.
- Бот с реальным токеном не запускался (токена нет); обработчики покрыты
  unit-тестами с моками Telegram API.
