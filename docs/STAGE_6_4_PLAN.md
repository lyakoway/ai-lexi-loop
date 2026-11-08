# Stage 6.4 — AI Tutor Chat: архитектура и план реализации

Статус: **план, ревью №4 внесено — ожидает согласования**. Production-код не
изменён, миграций нет, LLM-вызовы не выполнялись. Реализация — только после
ревью плана.

История ревью:
- Ревью №2: claim-based генерация, rate limit через `last_generation_at`,
  Idempotency-Key, SAVEPOINT-create, автозакрытие на лимите 100.
- Ревью №3: **fencing token `generation_claim_id`** — claim, сохранение пары
  и release проверяют владение клеймом; **claim_timeout** выведен из
  фактического бюджета `LlmClient` (initial + repair); единый
  idempotency-контракт Web (ключ обязателен, same-key+other-text → 409);
  правдивые формулировки дедупликации Telegram.
- Ревью №4 (🔄 помечено): analytics `tutor_chat_claim_lost` — **best-effort
  отдельной короткой транзакцией после rollback** сохранения; явная связь
  user→assistant **`in_reply_to_message_id`** + replay по этой связи с
  fingerprint текста; **валидация минимума `claim_timeout`** в конфигурации
  (меньше расчётного бюджета — ошибка старта); критерии 6.4.1/6.4.2
  разделены (claim lifecycle — в 6.4.2).

---

## 1. Краткий итог аудита

Проверено по фактическому коду (коммит `075af04`, рабочее дерево чистое):

| Компонент | Файл | Состояние для чата |
|---|---|---|
| LLM-контракт | `app/integrations/llm/client.py` | `StructuredClient.complete_structured` переиспользуется как есть |
| Prompt-инфраструктура | `app/services/tutor/prompts.py` | паттерн `build_*_messages` + `*_PROMPT_VERSION` повторяется для `tutor_chat_v1` |
| Explain | `app/services/tutor/service.py` | не расширяется: «объясни выражение» внутри чата решается самой моделью без tool calling |
| Generation | `app/services/tutor/generation.py` | паттерн «untrusted USER DATA + недоверенная situation» переиспользуется для сообщений пользователя |
| DI (Telegram) | `app/bot/middlewares/di.py` | добавляется одна запись `chat_service` (паттерн `generation_service`) |
| DI (API) | `app/api/deps.py` | добавляется `ChatDep` (паттерн `GenerationDep`) |
| Analytics | `app/services/analytics.py` | +константы событий, `track` не меняется |
| Телеметрия LLM | `app/integrations/llm/recorder.py` | работает как есть, `prompt_version` уже пишется |
| Telegram FSM-паттерны | `app/bot/handlers/{lesson,review,generate}.py` | token-защита callback и `safe_edit` переиспользуются |
| API-авторизация | `app/api/deps.py` | `CurrentUserId` переиспользуется |
| Хранилище диалогов | — | **отсутствует**, требуется миграция 0010 |
| Claim / rate limit / idempotency | — | отсутствуют, требуется детерминированный механизм (§9) |

Вывод: инфраструктура LLM, DI, аналитика и авторизация переиспользуются без
изменений; новое — хранение диалогов, `ChatService`, Context Builder,
Telegram-флоу и 6 API-endpoint'ов.

## 2. Текущая архитектура (фактические файлы)

- `app/bot/builder.py` — регистрация роутеров (10 роутеров, чат станет 11-м).
- `app/bot/middlewares/di.py` — транзакционная сессия на update + все сервисы.
- `app/api/routes/v1.py` — `/api/v1/...` с Bearer-авторизацией; ошибки — через
  `JSONResponse` (сохраняет analytics-события в транзакции).
- `app/core/metrics.py` — счётчики и гистограммы; LLM-метрики уже есть.
- `tests/` — 362 теста; паттерны FakeLlmClient, интеграционный conftest.

## 3. Предлагаемая архитектура AI Tutor Chat

Контролируемый конвейер (не агент): claim (fencing UUID) → сообщение
пользователя → Context Builder → один LLM-вызов (вне транзакции) со
structured output → детерминированное сохранение пары (user message,
assistant reply) с проверкой владения claim → рендер. LLM не имеет
инструментов и не меняет данные; всё состояние диалога пишет backend.

```
Telegram /chat (FSM)               API /api/v1/tutor/chat/*
        │                                │
        └───────────────┬────────────────┘
                        ▼
          ChatService (app/services/tutor/chat.py)
   get_or_create_active / send_message / close / list / history
        │ claim (короткий UPDATE, fencing UUID)  │ финальная транзакция:
        ▼                                       │ INSERT пары + UPDATE
  ContextBuilder                                │ conversations
  (профиль + последние                        │ WHERE generation_claim_id =
   N сообщений, budget)                       │  :claim_id  ← FENCING
        └────► LlmClient.complete_structured(ChatReplyOut, tutor_chat_v1)
               (ВНЕ транзакции; claim самос освобождается по таймауту)
```

## 4. Схема БД (миграция 0010, не создаётся на этапе планирования)

**`tutor_conversations`**

| Поле | Тип | Обоснование |
|---|---|---|
| id | BIGINT identity PK | существующее соглашение |
| user_id | BIGINT FK users ON DELETE CASCADE | изоляция; удаление пользователя стирает диалоги |
| language | VARCHAR(8) | целевой язык, фиксировался при создании |
| native_language | VARCHAR(8) | язык объяснений |
| level | VARCHAR(8) CHECK (A1–C2) | CEFR-снимок на момент старта |
| topic | VARCHAR(32) | тема/ситуация практики (nullable — свободный разговор) |
| situation | VARCHAR(300) | необязательное уточнение пользователя |
| status | VARCHAR(16) CHECK (active/closed) | один активный диалог на пользователя |
| message_count | INT default 0 | лимит истории (§5); инкремент атомарным UPDATE |
| generation_started_at | TIMESTAMPTZ NULL | claim: время захвата (старение для перехвата) |
| 🔄 generation_claim_id | UUID NULL | **fencing token**: владелец генерации; атомарно с `generation_started_at`; финальное сохранение и release проверяют владение (§9.1) |
| last_generation_at | TIMESTAMPTZ NULL | время последней попытки генерации — rate limit, включая неудачные вызовы (§9.2) |
| created_at / updated_at / closed_at | TIMESTAMPTZ | аудит |

Индексы:
- `UNIQUE (user_id) WHERE status='active'` — один активный диалог на
  пользователя; конкурентный create разрешается через SAVEPOINT (§9.4);
- `ix_tutor_conversations_user_status (user_id, status)` — список диалогов.

**`tutor_messages`**

| Поле | Тип | Обоснование |
|---|---|---|
| id | BIGINT identity PK | |
| conversation_id | BIGINT FK tutor_conversations ON DELETE CASCADE | каскад при удалении диалога |
| role | VARCHAR(16) CHECK (user/assistant) | |
| text | TEXT | содержимое (показывается только владельцу) |
| client_request_id | VARCHAR(64) NULL | Idempotency-Key пользователя (только role='user'); частичный UNIQUE (§9.3) |
| 🔄 in_reply_to_message_id | BIGINT NULL | только у assistant: ID user-сообщения, на которое отвечает (§9.5); у user-сообщений NULL |
| prompt_version | VARCHAR(32) | только у assistant — версионирование для eval |
| error_kind | VARCHAR(32) nullable | только у user: маркер неудачной попытки генерации (reply отсутствует) |
| created_at | TIMESTAMPTZ | порядок и rate limit |

Индексы и ограничения:
- `ix_tutor_messages_conversation_created (conversation_id, created_at)` —
  история и контекст;
- `UNIQUE (conversation_id, client_request_id) WHERE role = 'user' AND
  client_request_id IS NOT NULL` — идемпотентность (§9.3);
- 🔄 `UNIQUE (id, conversation_id)` — опорный ключ для составного FK;
- 🔄 `FOREIGN KEY (in_reply_to_message_id, conversation_id) REFERENCES
  tutor_messages (id, conversation_id)` — позиционное соответствие: первая
  колонка FK → `id` исходного user-сообщения, вторая → `conversation_id`;
  гарантирует, что assistant-сообщение ссылается на user-сообщение **того же
  conversation** (обычный FK по одному id этого не даёт); `ON DELETE CASCADE`
  (удаляется вместе с диалогом);
- 🔄 `CHECK ((role = 'user' AND in_reply_to_message_id IS NULL) OR
  (role = 'assistant' AND in_reply_to_message_id IS NOT NULL))` — user-сообщение
  никогда не имеет связи; assistant-сообщение не существует без связи;
- 🔄 `ix_tutor_messages_in_reply_to (in_reply_to_message_id)` — поиск ответа
  для idempotency replay (§9.3).

**Связь пары (🔄):** сохранение пары — сначала INSERT user-сообщения (получаем
его ID), затем INSERT assistant-сообщения с `in_reply_to_message_id` — в одной
транзакции. Idempotency replay находит assistant-ответ строго по связи с
user-сообщением, у которого совпадают `conversation_id`, `client_request_id` и
fingerprint (нормализованный текст) исходного сообщения; если связанный ответ
отсутствует — соседнее сообщение **не** подставляется (§9.3).

**Ключевые ограничения и поведение:**

- *Удаление пользователя* → CASCADE обоих таблиц.
- *Удаление/закрытие диалога* → DELETE/CLOSE активного диалога владельцем;
  чужой id → 404.
- *Незавершённая генерация*: пара (user, assistant) пишется одной финальной
  транзакцией только после успешного LLM-вызова **и только при владении
  claim** (fencing); при сбое транзакция откатывается целиком. Неудачная
  попытка фиксируется в analytics (`tutor_chat_message`, success=false), но не
  в истории диалога; повтор ограничен rate limit (§9.2).
- *Размер истории*: лимит **100 сохранённых сообщений всего (user и assistant
  вместе)** — §5.
- *Очистка старых сообщений*: retention-задача — вне Stage 6.4 (нет
  планировщика); ограничение сверху — лимит сообщений на диалог.

## 5. Семантика лимита истории

**Зафиксировано (подтверждено ревью №3):** лимит — **100 сохранённых сообщений
на диалог, суммарно (user + assistant)**. Пара, доводящая счётчик до лимита,
сохраняется последней; сразу после неё диалог автоматически закрывается
(`status='closed'`, `closed_at=now()` — в той же финальной транзакции), и
пользователю предлагается начать новый диалог. Автоматическое удаление старых
диалогов в Stage 6.4 **не входит**. Инкремент `message_count` — атомарный
`UPDATE ... SET message_count = message_count + 2` (не read-modify-write),
условие закрытия — в том же UPDATE.

## 6. Telegram UX

- `/chat` → приветствие + создание/возобновление активного диалога
  (`ChatService.get_or_create_active`), выбор темы кнопками (паттерн `/generate`,
  кнопка «🎲 Свободная тема») — состояние в FSM `ChatFlow`.
- Каждое следующее **текстовое сообщение** пользователя в состоянии
  `ChatFlow.active` уходит тьютору (без кнопок на каждый шаг). Не-текстовые
  сообщения (стикеры и т.п.) → мягкое напоминание «напишите текстом».
- Ответ — новое сообщение (не edit): `reply` + секции «🛠 Исправление»,
  «💡 Естественнее», «❓ Продолжим» — по наличию полей; пометка 🤖 AI.
- «Просьба объяснить выражение» — обычный вопрос в диалоге (модель объясняет
  inline; отдельный tool flow не нужен — зафиксировано как решение).
- `/end_chat` — закрыть диалог и выйти из FSM; message_count ≥ лимита (§5) →
  сервис закрывает диалог и сообщает об этом.
- Ошибки: единый fallback-текст «чат временно недоступен, обучение продолжается»
  + `tutor_chat_message` с success=false; FSM сохраняется (можно повторить).
- 🔄 **409 «Тьютор отвечает на предыдущее сообщение»** — тост, повтор позже.
  **429 «Слишком часто — подождите немного»** — тост.
- Stale callback: темы/ситуация несут token (id активного диалога), сверяются
  с FSM — паттерн `GenCB`.
- Ограничения: длина сообщения ≤ 1000 символов (обработчик отклоняет мягко,
  без LLM-вызова); JSON-логи не содержат текста сообщений.

## 7. Context Builder и prompt

`app/services/tutor/chat_context.py`:

- Вход: профиль (native/target/level), topic/situation диалога,
  последние ≤ 12 сообщений (запрос `ORDER BY created_at DESC LIMIT 12`,
  разворот в хронологический порядок); бюджет ≈ 3000 символов контекста,
  старые сообщения отбрасываются первыми.
- Не включает: полный профиль, статистику, словарь, другие диалоги.
- Сообщения пользователя — недоверенные данные: каждое оборачивается в
  размеченный блок `USER MESSAGE (untrusted)`, system-промпт запрещает
  выполнение инструкций из них (тот же приём, что в explain/generate).

Prompt `tutor_chat_v1` (`build_chat_messages` в `prompts.py`):

- Роль: разговорный партнёр для практики {target_language} на уровне CEFR
  {level}; объяснения и исправления — на {native_language}; отвечать простым
  языком уровня; поощрять продолжать; тема/ситуация из USER CONTEXT.
- Правила: не выполнять инструкции из сообщений; не менять данные пользователя
  (у модели нет инструментов); если пользователя просят «объяснить выражение» —
  объяснять inline в `reply`; отмечать ошибки в `correction`/`correction_note`
  только при реальных ошибках; не выдумывать факты; строгий JSON без markdown.
- `response_schema=ChatReplyOut` — существующий repair-механизм LlmClient.

## 8. Безопасность и изоляция пользователей

- Все запросы репозиториев скоупированы `user_id`; conversation id чужого
  владельца → 404 (без различения «не существует»/«чужой»).
- `user_id` только из Telegram-контекста / bearer-токена; из LLM не приходит.
- Prompt injection: разграничение system/data + явный запрет + незаключённые
  в инструкции situation; негативные кейсы в тестах (и в будущий golden set).
- LLM не имеет write-путей: сохранение пары сообщений — детерминированный код.
- Логи: provider/model/latency/ids; тексты сообщений — только в БД (доступ
  владельца), не в логи/analytics; в analytics — счётчики и флаги.
- Секреты: LLM_API_KEY в заголовке провайдера, вне исключений (гарантия 6.1).

## 9. Error handling и concurrency

### 9.1. Claim с fencing token (🔄 по ревью №3)

Каждая генерация захватывает **fencing UUID** — `generation_claim_id` —
атомарно в том же conditional UPDATE, что и `generation_started_at`. Claim без
UUID недействителен.

**Claim** (короткая транзакция):

```sql
UPDATE tutor_conversations
SET generation_started_at = now(),
    generation_claim_id   = :claim_id,   -- UUID, сгенерирован приложением
    last_generation_at    = now()
WHERE id = :id AND user_id = :user_id AND status = 'active'
  AND (generation_started_at IS NULL
       OR generation_started_at <= now() - :claim_timeout)
  AND (last_generation_at IS NULL
       OR last_generation_at <= now() - :min_interval)
RETURNING generation_claim_id;
```

- 0 строк → отказ (409 busy / 429 rate limit — причина по предшествующему
  SELECT'у состояния; гонка трактуется как 409).
- 1 строка → claim захвачен, `claim_id` известен вызывающему коду.

**LLM-вызов — вне транзакции** (соединение не занято секундами; обоснование
без изменения: удержание транзакции/lock на минуты блокирует vacuum и
соединения ради одного флага — fencing-строка решает это дешевле).

**Финальное сохранение** (одна транзакция, fencing через conditional UPDATE):

```sql
-- 1) INSERT user-сообщения и assistant-сообщения (обычные INSERT)
-- 2) UPDATE message_count + автозакрытие + release — ВЛАДЕНИЕ ОБЯЗАТЕЛЬНО:
UPDATE tutor_conversations
SET message_count = message_count + 2,
    status = CASE WHEN message_count + 2 >= :limit THEN 'closed' ELSE status END,
    closed_at = CASE WHEN message_count + 2 >= :limit THEN now() ELSE closed_at END,
    generation_started_at = NULL,
    generation_claim_id = NULL
WHERE id = :id AND generation_claim_id = :claim_id
RETURNING id;
```

- **1 строка** → claim всё ещё наш: COMMIT — пара сохранена.
- **0 строк** → claim перехвачен другим запросом (A → stale → B → …): typed
  результат `ChatUnavailable(reason="claim_lost")` → **ROLLBACK**: ни одного
  сообщения не сохранено, `message_count` не изменён, чужой claim не снят.
  HTTP **409**. Пользователю — «Диалог занят новым ответом, повторите
  сообщение».
- 🔄 **Analytics при claim_lost** — после полного ROLLBACK транзакции
  сохранения событие `tutor_chat_claim_lost` записывается **best-effort в
  отдельной короткой транзакции/сессии** (по образцу `LlmCallRecorder`).
  Ошибка записи аналитики не меняет результат `claim_lost`, не сохраняет
  устаревший ответ и не снимает чужой claim; неудача фиксируется безопасным
  логом без текста сообщений и секретов (§9.5, §11).

**Release после ошибки LLM** — тоже с fencing:

```sql
UPDATE tutor_conversations
SET generation_started_at = NULL, generation_claim_id = NULL
WHERE id = :id AND generation_claim_id = :claim_id;
```

- 0 строк → наш claim уже перехвачен: **ничего не делаем** — чужой claim не
  снимаем (лог `claim already taken, release skipped`).
- 1 строка → claim освобождён.

**Сценарий A → stale → B → A finishes**: claim_timeout истёк → B захватывает
claim (новый UUID) → A завершает LLM-вызов → финальный UPDATE A возвращает
0 строк → ROLLBACK → ответ A отброшен, B продолжает нормально. Ниже —
интеграционный тест этого сценария (§11).

### 9.2. Claim timeout — полный бюджет LlmClient (🔄 по ревью №3)

Проверено по фактическому коду Stage 6.1:

- каждый `complete()` ограничен жёстким дедлайном
  `llm_timeout_seconds × (llm_retry_attempts + 1)`; все retry, exponential
  backoff, `Retry-After` и fallback неподдерживаемого `response_format`
  (дополнительный HTTP-вызов) выполняются **внутри** этого дедлайна
  (delay клампится остатком бюджета);
- `complete_structured` выполняет **до двух полных `complete()`** — initial и
  repair-attempt при невалидном structured output, каждый со своим полным
  дедлайном.

Консервативный верхний бюджет одного `send_message`:

```
claim_timeout = ceil(2 × llm_timeout_seconds × (llm_retry_attempts + 1) × 1.1)
```

- множитель 2 — initial + repair; ×1.1 — запас на сетевые интервалы и
  запись в БД. При дефолтах (30 с × 3 попытки): `ceil(180 × 1.1) = 198 с`.
- 🔄 **Конфигурационный контракт**: `LLM_CHAT_CLAIM_TIMEOUT_SECONDS` —
  значение по умолчанию вычисляется в `Settings` по формуле выше из
  `llm_timeout_seconds` и `llm_retry_attempts`; ручное **увеличение**
  разрешено; ручное значение **меньше расчётного бюджета** отвергается
  валидацией конфигурации при старте (понятная ошибка
  `ValidationError`, не молча) — слишком маленький timeout позволил бы
  перехватывать claim до завершения штатного LLM-вызова. Пересчёт
  выполняется при изменении `llm_timeout_seconds`/`llm_retry_attempts`.
  Unit-тесты четырёх кейсов — §11.
- **Fencing остаётся обязательным независимо от точности бюджета**: модель-
  провайдер может превысить любые оценки — перехват просроченного claim
  обрабатывается typed-результатом `claim_lost`, а не падением.

### 9.3. Idempotency (единый контракт, 🔄 по ревью №3)

`POST /api/v1/tutor/chat/{id}/messages` требует заголовок `Idempotency-Key`
(строка 8–64 символа, `[A-Za-z0-9_-]`; генерируется клиентом, например UUID).

- **Отсутствие или некорректный формат → HTTP 422** (до клейма и LLM-вызова).
- **Повтор того же key с тем же текстом после успешного сохранения** →
  сохранённый ответ возвращается **без нового LLM-вызова**
  (заголовок `Idempotent-Replay: true`). 🔄 Поиск replay — строго по связи
  `in_reply_to_message_id`: находим user-сообщение с совпадающими
  `conversation_id`, `client_request_id` и fingerprint (нормализованный
  текст) исходного сообщения → берём **связанный** assistant-ответ. Соседнее
  или случайное сообщение как replay не используется.
- **Повтор того же key во время незавершённой генерации** → HTTP **409**
  («ответ ещё формируется»).
- **Повтор того же key с другим текстом сообщения** → HTTP **409** с detail
  «Idempotency-Key уже использован с другим сообщением». Старый ответ
  **не возвращается** — это был бы ответ на другой запрос пользователя.
- **Повтор после ошибки LLM** → key не сохранён → новая попытка разрешена.
- 🔄 **Key найден, но связанный assistant-ответ отсутствует** (историческая
  неудачная генерация/потерянная пара) → replay не выполняется: HTTP **409**
  «ответ для этого ключа не сохранён — повторите сообщение». Соседние
  сообщения никогда не подставляются.
- Telegram дедупликация: в нормальных условиях Telegram не доставляет один
  update дважды, но приложение **не проверяет `update_id`** и не тестирует
  exactly-once доставку — редкая повторная доставка может привести к
  повторному LLM-вызову; смягчение — rate limit (§9.2). Это зафиксированное
  ограничение, а не гарантия exactly-once.
- **Гарантия MVP (зафиксировано)**: *dedup результата* — повтор с тем же key
  никогда не создаёт дубликат пары в истории и возвращает сохранённый ответ
  для совпадающего текста. LLM-вызов — **at-least-once**: платный вызов может
  быть выполнен и не сохранён при сбое сервера; exactly-once для платных
  вызовов не гарантируется и не обещается.

### 9.4. Конкурентный `get_or_create_active`

- INSERT активного диалога выполняется в **SAVEPOINT**
  (`session.begin_nested()`): конфликт частичного `UNIQUE (user_id) WHERE
  status='active'` ловится как `IntegrityError`, `rollback_to_savepoint`
  восстанавливает сессию, затем повторный SELECT возвращает активный диалог,
  созданный соперником.
- Integration-тест: два конкурентных create (две сессии) → ровно один диалог,
  оба запроса получают один и тот же id.

### 9.5. Атомарность финальной транзакции и лимит

- Пара сообщений (сначала INSERT user-сообщения — получаем ID, затем INSERT
  assistant-сообщения с `in_reply_to_message_id`), затем fencing-условный
  UPDATE (инкремент счётчика, автозакрытие, release claim) — одна транзакция;
  0 строк от UPDATE = claim_lost → **ROLLBACK** (§9.1); сбой на любом шаге
  откатывает всё, включая обе вставки и связь пары.
- 🔄 Событие `tutor_chat_claim_lost` после ROLLBACK — best-effort в отдельной
  короткой транзакции (§9.1); сбой записи — лог, результат 409 не меняется.
- Инкремент: `SET message_count = message_count + 2`; закрытие при
  `message_count >= 100` — в том же UPDATE (§5).
- Тесты: сбой LLM не оставляет сообщений и снимает только свой claim;
  🔄 claim_lost-событие сохранено отдельной транзакцией после rollback;
  сбой записи analytics не меняет результат 409.

### 9.6. Прочие ошибки

- Уровни ошибок — существующая `LlmError`-иерархия; `ChatService.send_message`
  возвращает типизированный результат (`ChatExchange` / `ChatUnavailable[reason]`,
  включая `claim_lost`); хендлеры/API рендерят fallback без деталей.
- Таймауты/retries — политика LlmClient (6.1) без изменений.
- Транзакции: пара сообщений + счётчик + analytics — одна сессия.

## 10. Telemetry и evaluation

- Не дублируется: latency/tokens/ошибки уже в `llm_calls` (recorder).
- Новое в analytics: `tutor_chat_started` (topic), `tutor_chat_message`
  (success, error_kind, message_chars_bucket — только длина), `tutor_chat_closed`,
  `tutor_chat_busy` (счётчик 409), 🔄 `tutor_chat_claim_lost` (счётчик
  fencing-срабатываний; 🔄 записывается best-effort **отдельной короткой
  транзакцией после ROLLBACK** сохранения — неудача записи фиксируется
  логом и не влияет на результат 409).
- Prompt version `tutor_chat_v1` пишется в `llm_calls` (уже реализовано).
- Structured output статус — из `error_kind` (invalid_output) + метрики LlmClient.
- Feedback: отложено — 👍/👎 на ответах в evaluation-этапе (6.5) как отдельное
  событие без контента.
- Основа evaluation: `tests/data/tutor_chat_samples.json` (кейсы: простой
  диалог, фраза с ошибкой → correction заполнен, injection-сообщение → нет
  подчинения, запрос объяснения внутри чата). Полноценный judge — этап 6.5.

## 11. План тестирования

Unit (fake LLM, реальные контексты):
- Context Builder: бюджет, порядок сообщений, отсечение старых; untrusted
  оборачивание; профиль в system, не в data-блоках.
- ChatService: success-пара сохранена + claim снят; LLM disabled/error/
  invalid_output → `ChatUnavailable` + analytics success=false + release своего
  claim, история не изменена; 409 при активном чужом claim; 429 при
  неистёкшем interval; лимит сообщения 1000 символов; message_count ≥ лимита →
  автозакрытие; ownership (чужой conversation id → not found); инъекция.
- Схема `ChatReplyOut`: обязательный reply, опциональные поля, длины.
- Формула `claim_timeout` (🔄): равна `ceil(2 × timeout × (attempts+1) × 1.1)`
  при дефолтах конфига и пересчитывается при их изменении.
- Telegram хендлеры: /chat создание и возобновление; текст → ответ; не-текст
  → напоминание; длина > лимита → отказ без LLM; /end_chat; stale token.

Integration (PostgreSQL + fake LLM, сценарий A → stale → B → A finishes):
- 🔄 **Схема связи пары (Stage 6.4.1)**: составной FK
  `(in_reply_to_message_id, conversation_id) → (id, conversation_id)` запрещает
  ответ на user-сообщение **другого диалога**; CHECK запрещает
  assistant-сообщение без `in_reply_to_message_id` и user-сообщение со связью;
- 🔄 **Stale claim не может сохранить ответ**: claim A → принудительно
  протухнуть `generation_started_at` (тестовый UPDATE времени) → claim B
  захватывает новый UUID → финальная транзакция A (`claim_id` = A) → 0 строк →
  ROLLBACK: сообщений нет, `message_count` не изменён, analytics
  `tutor_chat_claim_lost`, заявлен `claim_lost`.
- 🔄 **Stale claim не может снять чужой claim**: release A после перехвата —
  `generation_claim_id` остаётся = B, `generation_started_at` не сброшен.
- 🔄 **Новый claim B успешно завершается после перехвата**: пара B сохранена,
  счётчик +2, claim снят.
- 🔄 **Повтор того же Idempotency-Key с тем же текстом** → тот же ответ, один
  LLM-вызов, `Idempotent-Replay: true`.
- 🔄 **Тот же key с другим текстом** → 409, старый ответ не возвращается.
- 🔄 **Тот же key во время генерации** → 409.
- 🔄 **Повтор после ошибки LLM** → новая попытка разрешена.
- 🔄 **Web API без `Idempotency-Key` / с некорректным форматом** → 422.
- 🔄 **`claim_timeout` соответствует фактическому бюджету** — unit-тест
  формулы против значений конфигурации.
- 🔄 **claim_lost-событие сохранено после ROLLBACK** основной транзакции
  (best-effort отдельной сессией).
- 🔄 **Сбой записи analytics при claim_lost** — результат 409 не меняется,
  чужой claim не снят, устаревший ответ не сохранён.
- 🔄 **Replay находит ответ по `in_reply_to_message_id`** (совпадение
  conversation_id + client_request_id + fingerprint текста).
- 🔄 **Replay при отсутствии связанного ответа** → 409, соседнее сообщение не
  подставляется.
- 🔄 **Конфигурация claim_timeout**: значение по умолчанию = формуле;
  ручное увеличение принимается; ручное значение меньше расчётного бюджета →
  `ValidationError` при старте; пересчёт при изменении
  `llm_timeout_seconds`/`llm_retry_attempts`.
- Конкурентный `get_or_create_active` (две сессии) → один диалог (SAVEPOINT).
- Параллельные `send_message` → один клейм побеждает, второй — busy.
- Атомарность: сбой LLM не оставляет сообщений; claim снят; событие сохранено.
- Автозакрытие на лимите 100.
- Изоляция: два пользователя; чужой id → 404.
- История: порядок, пагинация, контекст содержит последние сообщения.
- API endpoints сквозняком (401/404/409/422/429/503).
- Регресс: /lesson, /review, /generate не затронуты.

## 12. Последовательность реализации (подэтапы)

| # | Подэтап | Результат | Критерии приёмки |
|---|---|---|---|
| 6.4.1 | 🔄 Модели + миграция 0010 + **базовые** репозитории (без claim lifecycle) | `app/db/models/chat.py`, `app/repositories/chat.py` | up/down 10/10; схема, поля (`generation_claim_id`, `client_request_id`, `in_reply_to_message_id`), частичные UNIQUE, составной FK связи пары `(in_reply_to_message_id, conversation_id) → (id, conversation_id)` и CHECK согласованности `role`/связи, индексы, ownership-запросы, миграция — тестами; **без** claim lifecycle, fencing, rate limit, idempotency orchestration (они — 6.4.2) |
| 6.4.2 | 🔄 Схемы + Context Builder + prompt + **ChatService: атомарный claim, fencing (финал и release), rate limit, idempotency/replay, атомарная пара, автозакрытие** | `chat_outputs.py`, `chat_context.py`, `prompts.py+`, `chat.py` | unit+integration §11: fencing A→stale→B→A (старый claim не сохраняет и не снимает чужой), 409/429, идемпотентность/replay по `in_reply_to`, автозакрытие, изоляция, инъекция, формула и валидация claim_timeout |
| 6.4.3 | API endpoints | `v1.py`, `deps.py`, `schemas/api.py` | сквозные тесты; изоляция; 401/404/409/422/429/503; обязательный Idempotency-Key |
| 6.4.4 | Telegram /chat | `handlers/chat.py`, `keyboards/chat.py`, texts, builder, DI | FSM-тесты; регресс существующих сценариев |
| 6.4.5 | Analytics + README + gate | события, документация | полный gate, smoke с выключенной LLM |

Каждый подэтап — отдельная сдача с отчётом; изменение существующих файлов
минимально (builder/DI/deps/texts).

**Файлы по подэтапам:**
- 6.4.1: создать `app/db/models/chat.py`, `app/repositories/chat.py`
  (базовые CRUD/запросы без lifecycle-оркестрации),
  `migrations/versions/0010_tutor_chat.py`; изменить
  `app/db/models/__init__.py`, `app/repositories/__init__.py`.
- 6.4.2: создать `app/services/tutor/{chat.py,chat_context.py,chat_outputs.py}`;
  изменить `app/services/tutor/{prompts.py,__init__.py}`,
  `app/services/__init__.py`, `app/core/config.py`
  (+`LLM_CHAT_MIN_INTERVAL_SECONDS`, +`LLM_CHAT_CLAIM_TIMEOUT_SECONDS`
  с формулой-дефолтом и валидацией минимума).
- 6.4.3: изменить `app/api/deps.py` (+`ChatDep`), `app/api/routes/v1.py`
  (+6 endpoint'ов), `app/schemas/api.py` (+4 схемы).
- 6.4.4: создать `app/bot/handlers/chat.py`, `app/bot/keyboards/chat.py`;
  изменить `app/bot/{builder.py,texts.py,middlewares/di.py}`.
- 6.4.5: изменить `app/services/analytics.py`, `README.md`;
  создать `tests/data/tutor_chat_samples.json`.

## 13. Риски и ограничения

- **Стоимость/токены**: чат — самый частый LLM-сценарий; буферы: бюджет контекста,
  лимит сообщения, rate limit (§9.2 — покрывает и неудачные вызовы), claim
  исключает параллельные вызовы; дневные бюджеты пользователя — этап 6.6.
- **Не exactly-once LLM**: платный вызов может быть выполнен и не сохранён при
  сбое (§9.3) — зафиксированное ограничение MVP; fencing гарантирует, что
  потерянный/устаревший результат не попадёт в историю.
- **Stale claim (крайний случай)**: сбой процесса между claim и release
  блокирует диалог до истечения `claim_timeout` (≈200 с с формулой §9.2) —
  пользователь видит честный 409; перехват по таймауту обрабатывается fencing'ом.
- **В памяти FSM vs БД**: активный диалог живёт в БД (не теряется при рестарте,
  в отличие от lesson/generate draft-превью) — осознанное отличие, т.к. чат
  многодневный.
- **Latency**: один-два LLM-вызова на сообщение (initial + возможный repair);
  Telegram-индикация «печатает» — `send_chat_action` (интерфейсный слой).
- **Retention**: без планировщика старые закрытые диалоги не удаляются
  автоматически (лимит 100 сообщений на диалог ограничивает рост); задача
  фиксируется для этапа с планировщиком (Stage 7).
- **Модерация контента**: MVP полагается на системные правила модели;
  отдельного фильтра нет — зафиксировано как ограничение.
- **Один активный диалог**: без параллельных диалогов по темам (упрощение MVP;
  схема допускает расширение снятием частичного UNIQUE).
- **Telegram дедупликация**: exactly-once доставки не гарантируется
  приложением (§9.3); редкий повтор update может дать повторный LLM-вызов.

## 14. Критерии готовности Stage 6.4

- Пользователь может вести многоходовой диалог `/chat` на изучаемом языке;
  ответы соответствуют CEFR; исправления и варианты рендерятся при наличии.
- История диалога переживает рестарты; контекст ограничен бюджетом.
- Не более одной активной генерации на диалог; второй параллельный запрос
  получает 409; claim освобождается (финал/ошибка/таймаут).
- **Fencing**: stale claim не может сохранить сообщения, изменить счётчик или
  снять чужой claim; новый claim завершается после перехвата (сценарий
  A → stale → B → A — интеграционным тестом).
- 🔄 **claim_lost-аналитика** записывается после ROLLBACK отдельной
  best-effort транзакцией; сбой записи не меняет результат 409.
- Rate limit ограничивает и успешные, и неуспешные вызовы (429).
- Идемпотентный повтор с тем же `Idempotency-Key` и тем же текстом возвращает
  сохранённый ответ **по связи `in_reply_to_message_id`**; same-key+other-text →
  409; ключ обязателен для Web (422); replay без связанного ответа → 409;
  ограничение at-least-once LLM задокументировано.
- Лимит 100 сообщений (суммарно) закрывает диалог автоматически и атомарно.
- Изоляция: чужие диалоги/сообщения недоступны; инъекционные кейсы покрыты.
- Telegram и API используют один `ChatService`; дублирования логики нет.
- LLM не может менять профиль/выражения/прогресс/SRS; сохранение — только
  детерминированный backend.
- `claim_timeout` покрывает фактический бюджет `LlmClient` (unit-тест
  формулы); 🔄 ручное значение меньше расчётного бюджета отвергается
  валидацией конфигурации при старте.
- Полный gate зелёный; миграция 0010 up/down; приложение работает с
  выключенной LLM (чат деградирует честным сообщением).
- Все существующие тесты (362) остаются зелёными; новые — по §11.
- Коммит/пуш — только по отдельной команде.

## 15. Решения, требующие согласования (🔄 обновлено ревью №4)

1. **Fencing-схема** (§9.1): `generation_claim_id UUID`, финальное сохранение
   и release — только при владении claim; stale → `claim_lost` (409) без
   сохранения.
2. **`claim_lost`-аналитика** (§9.1/§9.5/§10): best-effort **отдельной
   короткой транзакцией после ROLLBACK**; сбой записи — лог, результат 409
   не меняется.
3. **Связь пары `in_reply_to_message_id`** (§4/§9.5): составной FK
   `(in_reply_to_message_id, conversation_id)` → `tutor_messages (id,
   conversation_id)` + CHECK согласованности `role`/связи;
   replay строго по связи + fingerprint текста; отсутствие связанного
   ответа → 409 без подстановки соседних сообщений.
4. **Валидация минимума `claim_timeout`** (§9.2): ручное значение меньше
   `ceil(2 × timeout × (attempts+1) × 1.1)` — `ValidationError` при старте;
   увеличение разрешено; пересчёт при изменении `llm_timeout_seconds`/
   `llm_retry_attempts`.
5. Подтверждено ранее (вне согласования): автозакрытие на 100 суммарных
   сообщениях; five подэтапов; без Redis/LangGraph/LangChain; rate limit
   параметры (`LLM_CHAT_MIN_INTERVAL_SECONDS=3`, 409/429 семантика).
