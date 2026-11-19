# Telegram Knowledge Collector — разбор ТЗ, архитектурное предложение и план

> Статус: **предложение**, не реализация. Составлено по `prompt.md` (1089 строк).
> Документ фиксирует: что уже решено, что предлагается по умолчанию, что нужно подтвердить.
> После согласования решения переносятся в `docs/adr/NNNN-*.md` и в README.

---

## 1. Резюме

Задача — не «бот, который пересылает сообщения в Markdown», а **конвейер с гарантиями сохранности**:
принять → надёжно сохранить оригинал → нормализовать → собрать заметку → (опционально) обогатить LLM →
опубликовать в S3 → сверять состояние и **никогда молча не затирать правки пользователя**.

Отсюда три архитектурных следствия, которые определяют всё остальное:

1. **`kb_id` и «публикация = фиксация» вводятся в первом же вертикальном срезе**, а не после генератора заметок.
2. **Очередь и архив — в SQLite/на диске, а не в памяти процесса**; любой шаг идемпотентен и повторяем.
3. **S3 — источник истины о содержимом vault, БД — источник истины об идентичности и истории.**
   Всё, что расходится, разрешается в пользу пользователя, а не в пользу «правильной» версии приложения.

---

## 2. Что зафиксировано окончательно (не переспрашиваю)

| Область | Решение |
|---|---|
| Форма | Модульный монолит на Python, без микросервисов, Redis, Celery, внешней БД |
| Хранилище состояния | SQLite на persistent volume + миграции схемы |
| Очередь | Собственная, устойчивая, в таблице БД (lease, retry, backoff) |
| Приём | Bot API long polling, allowlist по числовым ID, один получатель updates |
| Первичный импорт | HTML-экспорт Telegram Desktop + каталоги вложений (без предварительной конвертации) |
| Публикация | S3-совместимое хранилище, раздельные префиксы vault / archive / backup |
| Клиент | Obsidian + сторонний плагин синхронизации (приложение его не заменяет) |
| Модель заметки | Одна заметка ← несколько исходных сообщений; связь в БД |
| Идентичность | `kb_id` в front matter, неизменяемый, не выводится из пути |
| Редактирование | После первой публикации — только предложения в `Review/`, применение отдельным действием |
| LLM | Необязательное обогащение; при отказе публикуется полноценная базовая заметка |
| Развёртывание | Docker Compose, Ubuntu 24.04; отладка локально на Windows |
| Вне MVP | OCR, транскрибация, загрузка веб-страниц, embeddings/RAG, пользовательская Telegram-сессия, веб-UI |

---

## 3. Открытые вопросы и предлагаемые значения по умолчанию

Формат: **вопрос → предлагаемое значение**, чтобы можно было согласовать «по списку», а не по одному.

### 3.1. Блокирующие (без них нельзя начинать Этап 3)

| № | Вопрос | Предлагаю по умолчанию |
|---|---|---|
| B1 | S3-провайдер, endpoint, region, bucket, префиксы | Проектирую под S3-совместимый API; конкретика нужна для spike. Префиксы: `vault/`, `archive/`, `backup/` внутри одного bucket |
| B2 | Плагин синхронизации Obsidian и его версия | Нужен только для spike §14.1; до ответа работаю с локальной заглушкой хранилища (filesystem+S3-адаптер) |
| B3 | LLM-провайдер и модель | Провайдер-агностичный интерфейс + OpenAI-совместимый HTTP-адаптер (подходит OpenAI, OpenRouter, Ollama, vLLM). Модель — параметр конфигурации |
| B4 | Тип целевого чата и ID разрешённых пользователей/чатов | Конфиг `allowlist` пустой по умолчанию → бот отклоняет всех; заполняется вами в `.env` |
| B5 | Рубрикатор (темы/глубина) | Пример на 2 уровня в `config/rubricator.yaml`, темы — заготовка; LLM выбирает **только из** списка, иначе `Inbox` |

### 3.2. Не блокирующие (можно принять позже, до соответствующего этапа)

| № | Вопрос | Предлагаю по умолчанию |
|---|---|---|
| N1 | Текущий обработчик бота (webhook / n8n / другое) | Проверка `getWebhookInfo` перед запуском; webhook не удаляю автоматически, только сообщаю |
| N2 | Объём истории, частота, размеры вложений | Импорт потоковый; лимит одного файла — 20 МБ (ограничение Bot API `getFile`, требует проверки актуальности) |
| N3 | Устройства с Obsidian | На результат не влияет, влияет только на spike |
| N4 | Требования к клиентскому шифрованию | Считаю выключенным до отдельного решения; шифрование плагина несовместимо с обычными Markdown-объектами без отдельного исследования |
| N5 | Правила объединения сообщений | Только достоверные сигналы: `media_group_id`, точный `reply_to`, ручная команда. Время/автор — не основание |
| N6 | Обязательные типы вложений | Фото, документы, аудио, видео, голосовые; список в конфиге, остальное → `unsupported` |
| N7 | Прокси | Не предполагаю; поддержка через `HTTPS_PROXY` у httpx/boto3 |
| N8 | Допустимая задержка публикации | Цель: ≤ 2 мин от приёма до появления в S3 (без учёта синхронизации Obsidian), окно ожидания альбома — 60 с |
| N9 | Догрузка пропущенных updates после простоя | Не обещаю; фиксирую `last_update_id` и явно сообщаю о разрыве |
| N10 | Канал эксплуатационных уведомлений | Только логи stdout + `status`/`doctor`; Telegram-уведомления — опционально позже |

**Не прошу секреты в переписке.** Всё через `.env` по образцу `.env.example`.

### 3.3. Подтверждено пользователем 2026-11-16

| Вопрос | Решение |
|---|---|
| Префикс vault | `vault4-kb01` (bucket `obsidian-kb01`) |
| S3-провайдер | Selectel Object Storage, `https://s3.ru-7.storage.selcloud.ru`, регион `ru-7` |
| Плагин синхронизации | Remotely Save **0.5.25**, клиентское шифрование выключено |
| LLM | `https://api.deepseek.com`, модель `deepseek-flash` (доступность проверена) |
| Telegram allowlist | 1 пользователь, 1 чат; существующего обработчика нет |
| Рубрикатор | старт с `Inbox`, темы добавляются позже |
| Отладка | локально на Windows, без Docker |
| Личные данные | экспорт — в `mydata/`, вне Git |

Результаты проверок: `docs/reports/stage-2.md`; решения — ADR 0001–0005.

---

## 4. Предлагаемые архитектурные решения (заготовки ADR)

| ADR | Решение | Обоснование |
|---|---|---|
| 0001 | Модульный монолит, слои `domain / services / adapters`, один процесс-воркер | ТЗ требует; альтернативы (очередь в Redis, отдельный планировщик) нарушают ограничение «без избыточной инфраструктуры» |
| 0002 | SQLAlchemy 2.0 (Core+ORM) + Alembic | Миграции обязательны; Alembic — стандарт де-факто. Отказ от «сырого» sqlite3 даёт предсказуемые транзакции и тестируемость |
| 0003 | SQLite в режиме WAL, `busy_timeout`, `foreign_keys=ON`, один писатель (воркер) | Устраняет `database is locked`; запись сериализуется очередью, чтение — параллельно |
| 0004 | Приём: `aiogram` как клиент Bot API + **собственный цикл `getUpdates`** | Нужен контроль «сохранить в БД → потом сдвинуть offset». Готовый `Dispatcher.start_polling` эту гарантию не документирует; собственный цикл ~100 строк и проверяем тестом |
| 0005 | `boto3` (синхронный, в thread pool) | `aiobotocore` усложняет и отстаёт по поддержке; узкое место — сеть, а не CPU |
| 0006 | Условные записи S3 (`If-Match`/`If-None-Match`) **только после spike**; иначе ручное применение | Поддержка у S3-совместимых провайдеров неоднородна. Небезопасный overwrite недопустим по ТЗ §14.4 |
| 0007 | LLM: свой `Protocol` + HTTP-адаптер OpenAI-compatible, кэш по (prompt_version, model, params, input_hash) | Независимость от вендора, тестируемость, воспроизводимость |
| 0008 | `kb_id = "note_" + uuid7` | Монотонный, сортируемый, генерируется локально, не требует координации |
| 0009 | Имена файлов: `slug(title)` + короткий суффикс `kb_id`, `allow_unicode=True` | ТЗ требует кириллицу/emoji и совместимость с Windows; стабильность важнее красоты, путь после публикации не меняется |
| 0010 | Пакетный менеджер `uv`, Python 3.13 | ТЗ требует lockfile; uv даёт `uv.lock` и одинаковое поведение на Windows/Ubuntu |
| 0011 | Логи: `structlog` → JSON в stdout, с фильтром маскирования `bot<token>` и текстов сообщений | ТЗ §16/§17 |
| 0012 | Конфигурация: `pydantic-settings` (.env) + YAML для рубрикатора/шаблонов | Разделение секретов и версионируемых артефактов |

### Важное замечание по версии Python

Текущий `.venv` создан на **Python 3.15.2**. Это свежий интерпретатор, и wheels для `pydantic-core`,
`lxml`, `orjson`, `uuid-utils` под него могут отсутствовать — сборка из исходников на Windows
потребует MSVC. Целевая Ubuntu 24.04 даёт 3.12, Docker-образ — 3.13.

**Предлагаю:** зафиксировать `requires-python = ">=3.13,<3.14"`, пересоздать `.venv` на 3.13
(он уже установлен: `C:\Users\all\AppData\Local\Programs\Python\Python313`), Docker — `python:3.13-slim`.

---

## 5. Структура репозитория (src-layout)

```
VPb01/
├── pyproject.toml
├── uv.lock
├── README.md
├── CHANGELOG.md
├── .env.example
├── .gitattributes
├── .gitignore
├── Dockerfile
├── compose.yaml
├── config/
│   ├── rubricator.yaml            # версионируемый рубрикатор (темы L1/L2)
│   ├── app.example.yaml           # пример настроек обработки
│   └── prompts/
│       ├── classify.v1.md         # промпты = версионируемые артефакты
│       ├── summarize.v1.md
│       └── group_candidates.v1.md
├── docs/
│   ├── analysis-and-plan.md       # этот документ
│   ├── architecture.md
│   ├── editing-policy.md
│   ├── import.md
│   ├── s3-obsidian.md             # отчёт о совместимости (spike)
│   ├── operations.md
│   ├── backup-restore.md
│   └── adr/
│       └── 0001-....md
├── src/kbcollector/
│   ├── __main__.py                # точка входа CLI
│   ├── config.py                  # pydantic-settings, загрузка .env + YAML
│   ├── logging.py                 # structlog, redaction
│   ├── domain/                    # чистые модели, без I/O
│   │   ├── ids.py                 # uuid7, kb_id, attachment_id
│   │   ├── message.py             # RawMessage, MessageVersion, Entity
│   │   ├── note.py                # Note, NoteState, Publication
│   │   ├── group.py               # MessageGroup, GroupSignal
│   │   ├── proposal.py            # Proposal, ProposalKind, ProposalState
│   │   └── paths.py               # safe path/slug политика
│   ├── db/
│   │   ├── engine.py              # подключение, PRAGMA, session factory
│   │   ├── models.py              # SQLAlchemy-таблицы
│   │   ├── repositories/          # note_repo, message_repo, job_repo, ...
│   │   └── migrations/            # Alembic env + versions/
│   ├── ingestion/
│   │   ├── telegram/
│   │   │   ├── poller.py          # getUpdates + транзакционное подтверждение
│   │   │   ├── allowlist.py
│   │   │   ├── mapper.py          # Update → RawMessage (+ payload JSON)
│   │   │   └── downloader.py      # getFile, потоковая загрузка
│   │   └── html_export/
│   │       ├── discovery.py       # поиск страниц/каталогов, границы экспорта
│   │       ├── parser.py          # BeautifulSoup-разбор структуры сообщений
│   │       ├── entities.py        # <a>, <code>, <pre>, emoji, переносы
│   │       ├── pages.py           # порядок страниц и сообщений
│   │       └── report.py          # отчёт о потерях/неоднозначностях
│   ├── normalization/
│   │   ├── text.py                # канонизация, UTF-16 offsets, hash версии
│   │   └── dedupe.py              # технические дубли vs повторные пересылки
│   ├── archive/
│   │   └── raw_store.py           # неизменяемое хранение payload/экспорта + манифест
│   ├── attachments/
│   │   ├── policy.py              # типы, лимиты, статусы
│   │   ├── naming.py              # безопасные имена, дедуп по sha256
│   │   └── store.py               # локальное хранение до публикации
│   ├── grouping/
│   │   ├── signals.py             # reply / media_group / manual
│   │   └── grouping.py            # правила + окно ожидания альбома
│   ├── classification/
│   │   ├── rubricator.py          # загрузка и валидация рубрикатора
│   │   └── fallback.py            # Inbox при неуверенности/ошибке
│   ├── llm/
│   │   ├── base.py                # Protocol: complete_structured(...)
│   │   ├── openai_compat.py       # HTTP-адаптер
│   │   ├── prompts.py             # версии, загрузка, рендер
│   │   ├── cache.py               # кэш ответов в БД
│   │   └── guard.py               # лимиты входа, таймауты, валидация схемы
│   ├── markdown/
│   │   ├── frontmatter.py         # YAML escaping, kb_id, порядок ключей
│   │   ├── renderer.py            # Jinja2, секции, code fences, ссылки
│   │   ├── sanitize.py            # небезопасный HTML, управляющие символы
│   │   └── templates/
│   │       ├── note.md.j2
│   │       └── proposal.md.j2
│   ├── publication/
│   │   ├── layout.py              # vault/archive пути, имена объектов
│   │   ├── s3_client.py           # boto3, таймауты, retry, pagination
│   │   ├── publisher.py           # порядок: вложения → проверка → Markdown
│   │   └── conditional.py         # If-Match/If-None-Match, ETag vs sha256
│   ├── notes/
│   │   ├── registry.py            # реестр kb_id ↔ путь ↔ состояние
│   │   ├── conflicts.py           # duplicate/missing kb_id
│   │   └── apply.py               # подтверждённое применение предложений
│   ├── review/
│   │   └── proposals.py           # Review/<kb_id>/<proposal_id>.md
│   ├── reconcile/
│   │   ├── scanner.py             # инкрементальный обход префикса vault
│   │   └── diff.py                # перемещение/изменение/missing/дубли
│   ├── jobs/
│   │   ├── queue.py               # enqueue/claim/lease/complete/fail
│   │   ├── backoff.py
│   │   ├── worker.py              # цикл, graceful shutdown
│   │   └── handlers/              # save_attachment, build_note, enrich, publish, reconcile
│   ├── diagnostics/
│   │   ├── health.py              # прогресс, а не «процесс жив»
│   │   └── status.py              # метрики из §16
│   ├── backup/
│   │   ├── sqlite_backup.py       # sqlite3 .backup / VACUUM INTO
│   │   └── restore.py             # явная операция, без неявного запуска
│   └── cli/
│       ├── app.py                 # Typer-приложение
│       └── commands/
│           ├── run.py  import_html.py  status.py  doctor.py
│           ├── retry.py  reprocess.py  reconcile.py
│           └── proposals.py  restore.py  backup.py
└── tests/
    ├── unit/                      # парсер, slugify, frontmatter, backoff, dedupe
    ├── integration/               # БД+очередь, S3 на moto, HTTP на respx
    ├── e2e/                       # импорт → файл → «S3» → сверка
    └── fixtures/
        ├── html/                  # обезличенные страницы экспорта
        ├── telegram/              # update-json'ы
        └── s3/                    # ожидаемые объекты
```

**Почему так:**
- `domain/` без I/O → правила идентичности и путей тестируются мгновенно и не зависят от S3.
- `jobs/handlers/` — единственное место, где шаги конвейера касаются внешнего мира.
- `config/prompts/` и `config/rubricator.yaml` версионируются вместе с кодом: изменение промпта
  меняет `input_hash` кэша, но **не** перезаписывает опубликованные заметки (только `Review/`).

---

## 6. Модель данных (SQLite)

Смысловое разделение из §10.1, но без «таблицы на каждое слово».

| Таблица | Назначение | Ключевые ограничения |
|---|---|---|
| `deliveries` | факт доставки update | `UNIQUE(source, update_id)` → повтор не создаёт запись |
| `sources` | запуски импорта/источники | kind, путь, статистика, статус |
| `raw_messages` | исходные сообщения | `UNIQUE(source_kind, chat_id, message_id)` |
| `message_versions` | версии текста/entities | hash, fetched_at; правка сообщения = новая версия |
| `id_map` | сопоставление HTML↔live | method, confidence, `status ∈ explicit/manual/candidate` |
| `attachments` | вложения и статусы | `sha256`, `status ∈ missing/oversized/unsupported/download_failed/stored/uploaded` |
| `message_groups` / `group_members` | единица заметки | signal ∈ album/reply/manual |
| `notes` | заметки | `kb_id` PK, `current_path`, `state ∈ draft/published/missing/conflict` |
| `note_sources` | связь заметка↔сообщения | M:N, явное присоединение |
| `generations` | результаты LLM | prompt_version, model, params, `input_hash` |
| `proposals` | предложения в `Review/` | `base_etag`, `base_sha256`, `base_path`, state |
| `publications` | факты публикации | object_key, sha256, etag, версия |
| `observations` | наблюдённое состояние S3 | etag/size/существует/`kb_id` |
| `conflicts` | конфликты идентичности | duplicate_kb_id / missing_kb_id / stale_base |
| `jobs` | очередь | состояние, lease, attempts, next_run_at, `dedupe_key` |
| `meta` | служебное | schema_version, last_update_id, last_import_at |

Конвенции:
- ID — `uuid7` (строкой) для сущностей, `kb_id` — `note_<uuid7>`.
- Время — UTC `datetime`, сериализация ISO 8601 с `Z`.
- JSON-поля — `TEXT` с `json_valid()`-проверкой (SQLite JSON1 доступен в 3.38+).
- Удаление — только физическое для служебных записей; пользовательские данные не «чистим» молча.

### 6.1. Очередь заданий

`jobs(state, run_at, lease_until, attempts, last_error, dedupe_key)`.

- Захват: `BEGIN IMMEDIATE` → `UPDATE ... SET state='processing', lease_until=... WHERE id IN (...) RETURNING`.
- Возврат зависших: `state='processing' AND lease_until < now` → `retry`.
- Повторы: `min(base * 2^attempts, cap)` + jitter; учёт `retry_after` сервера (Telegram/S3/LLM).
- `dedupe_key` делает постановку задания идемпотентной (`note:build:<kb_id>:<inputs_hash>`).
- **Статусы обогащения и публикации независимы:** отказ LLM не блокирует публикацию базовой заметки.
- Exactly-once не обещаем; обещаем идемпотентные эффекты и повторяемость (§10.3).

---

## 7. Ключевые потоки

### 7.1. Live ingestion

```
getUpdates(offset=last+1, timeout=50)
  → фильтр allowlist (иначе drop + лог)
  → BEGIN: deliveries + raw_messages + message_versions + jobs(enqueue)
  → COMMIT
  → только теперь last_update_id = update_id
```

Правило: **offset сдвигается после commit**, а не после обработки. Смысл semantics aiogram проверяется
spike'ом (§7 ТЗ), поэтому цикл пишем сами поверх `Bot.get_updates`.

### 7.2. Импорт HTML

`discovery → parser (постранично, потоково) → normalization → raw_messages (+ attachments) → grouping → note → publish`.
Возобновление: checkpoint по `(source_id, page_index, last_message_ordinal)` в БД; повторный запуск
продолжает, а не начинает заново. Dry-run — тот же конвейер без `publication`.

### 7.3. Публикация

1. Вложения в `archive`/`vault` (по политике) → проверка `HEAD` (size/sha256).
2. Markdown last, ссылки только на подтверждённые объекты; недоступное помечается явно.
3. `publications` фиксируется в той же транзакции, что и `notes.current_path`.
4. Повторный PUT идёт в **тот же** object key (никаких случайных имён) — идемпотентность.

### 7.4. Сверка

- Инкрементально: `ListObjectsV2` + сравнение `etag/size/last_modified` с `observations`
  (этag — **подсказка**, не hash содержимого).
- `GET` только для объектов, где подсказка говорит «изменилось», либо при полной проверке.
- Перемещение: найден другой ключ с тем же `kb_id` → обновить `current_path`.
- Пропажа: `state='missing'` + повторная проверка → не восстанавливать.
- Полная сверка — по расписанию (например, раз в сутки) и по команде `reconcile --full`.

---

## 8. Зависимости

### 8.1. Runtime

| Пакет | Роль | Почему он / альтернатива |
|---|---|---|
| `aiogram` (3.x) | типы Bot API, `getFile`, скачивание | зрелый, типизированный; альтернатива — голый httpx + свои модели (лишний код) |
| `httpx` | HTTP к LLM, скачивание файлов | уже в зависимостях aiogram; таймауты/прокси |
| `beautifulsoup4` + `lxml` | разбор HTML-экспорта | `lxml` быстрый и даёт корректное дерево; регулярки запрещены ТЗ |
| `pydantic` (2.x) | доменные схемы, валидация ответов LLM | строгая валидация структурированного вывода |
| `pydantic-settings` | конфиг из `.env` + YAML | типизированные настройки, секреты не в коде |
| `SQLAlchemy` (2.0) | доступ к БД | транзакции, `RETURNING`, тестируемость |
| `alembic` | миграции | требование ТЗ |
| `boto3` (+ `boto3-stubs`) | S3 | стандарт; условные записи проверяем spike'ом |
| `python-frontmatter` | чтение/запись YAML front matter | ровно задача из ТЗ; `ruamel.yaml` — если понадобится сохранять комментарии |
| `PyYAML` | рубрикатор, шаблоны | `safe_load`/`safe_dump(sort_keys=False, allow_unicode=True)` |
| `Jinja2` | шаблоны заметок и предложений | отделяет формат от логики |
| `typer` + `rich` | CLI, прогресс, отчёты | требования §16 (прогресс, dry-run, отчёты) |
| `structlog` | структурные логи | JSON в stdout, redaction-процессор |
| `tenacity` | ретраи внешних вызовов | дополняет очередь (внутрипроцессные повторы) |
| `python-slugify` | безопасные имена файлов | `allow_unicode=True`, транслитерация как fallback |
| `pathvalidate` | проверка Windows-путей и длины | ТЗ §11.3 (запрещённые символы, лимиты) |
| `uuid-utils` | `uuid7` | монотонные ID; fallback — `uuid6` |

### 8.2. Dev / test

`pytest`, `pytest-asyncio`, `pytest-cov`, `respx` (мок httpx), `moto[s3]` (S3),
`freezegun`/`time-machine` (время), `syrupy` (снапшоты Markdown), `hypothesis` (свойства путей/парсера),
`ruff` (lint+format), `mypy` (типы), `pre-commit`.

### 8.3. Схема `pyproject.toml` (без пинов — их зафиксирует `uv lock`)

```toml
[project]
name = "kbcollector"
requires-python = ">=3.13,<3.14"
dependencies = [
  "aiogram>=3.13,<4",
  "httpx>=0.27",
  "beautifulsoup4>=4.12",
  "lxml>=5.2",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "boto3>=1.34",
  "python-frontmatter>=1.1",
  "PyYAML>=6.0",
  "Jinja2>=3.1",
  "typer>=0.12",
  "rich>=13.7",
  "structlog>=24.1",
  "tenacity>=8.5",
  "python-slugify>=8.0",
  "pathvalidate>=3.2",
  "uuid-utils>=0.9",
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "pytest-cov>=5", "respx>=0.21",
       "moto[s3]>=5", "freezegun>=1.5", "syrupy>=4", "hypothesis>=6",
       "ruff>=0.6", "mypy>=1.11", "boto3-stubs[s3]>=1.34", "pre-commit>=3.7"]

[tool.ruff]
line-length = 100
target-version = "py313"

[project.scripts]
kbcollector = "kbcollector.__main__:app"
```

> `uv` кладёт `uv.lock` в корень — он коммитится (это уже разрешено в `.gitignore`).

---

## 9. План реализации

Этапы соответствуют §21 ТЗ. Каждый этап заканчивается отчётом: что сделано, как запустить,
что проверено тестами, что проверено только на моках, какие ограничения остались.

### Этап 0. Каркас (0.5–1 день)

- `pyproject.toml`, `uv.lock`, `.env.example`, `ruff`/`mypy`/`pre-commit`, `pytest` скелет.
- `config.py`, `logging.py`, каркас Typer (`kbcollector --help`, `doctor` заглушка **не** выдаёт себя за работающую).
- `Dockerfile` + `compose.yaml` (non-root, volume, healthcheck, `stop_grace_period`).
- **Выход:** `uv run pytest` зелёный, `docker compose config` валиден.

### Этап 1. Аудит и решения

- Исследование реального HTML-экспорта (нужен образец!) → `docs/import.md` с фактической структурой.
- ADR 0001–0012, `docs/editing-policy.md`, перечень угроз потери данных.
- Список открытых вопросов с ответами (по §3).
- **Выход:** согласованные ADR + подтверждённые критерии MVP.

### Этап 2. Спайки (блокируют Этап 3 частично)

| Спайк | Проверяем | Артефакт |
|---|---|---|
| S1. S3 ↔ Obsidian | PUT из Python → появление на устройстве, кириллица, вложенные пути, переименование, удаление, конфликт | `docs/s3-obsidian.md` |
| S2. Условные записи | `If-Match`/`If-None-Match` у выбранного провайдера | решение по ADR 0006 |
| S3. HTML | реальные страницы, ID, reply, forwards, code | `docs/import.md`, fixtures |
| S4. ID-сопоставление | HTML `id` vs Bot API `message_id` | правила или «только ручное» |
| S5. Offset semantics | что подтверждается и когда | тест + ADR 0004 |

Без доступа к сервисам спайки помечаются **«не проверено»** — и это отражается в отчёте, а не маскируется моками.

### Этап 3. Вертикальный срез без LLM (главный)

```
один материал (HTML) / один update → raw архивирование → заметка с kb_id
→ локальный fake-S3 → реальный S3 (если S1 готов)
```

- `raw_messages`, `notes`, `publications`, `jobs` + первая миграция Alembic.
- Рендер заметки по шаблону §11.2; `kb_id` в front matter; запрет перезаписи уже здесь.
- Публикация с порядком «вложения → проверка → Markdown».
- **Выход:** E2E-тест «импорт → файл на диске/S3», повторный запуск не создаёт дубль.

### Этап 4. Надёжный импорт и вложения

- Пакетный импорт с checkpoint'ами, dry-run, прогрессом и отчётом о потерях.
- Вложения: статусы, лимиты, потоковое скачивание, sha256, безопасные имена.
- Очередь: lease, retry, backoff+jitter, ручной `retry`, классификация ошибок.
- Тесты аварийных остановок (§19.3) — именно здесь.
- **Выход:** прерванный импорт продолжается; повтор не создаёт технических дублей.

### Этап 5. Организация знаний

- Рубрикатор YAML + правила выбора; LLM только выбирает из списка, иначе `Inbox`.
- Группировка по достоверным сигналам; окно ожидания альбома.
- LLM-обогащение: заголовок, резюме, теги, предложение связей; кэш и лимиты.
- Предложения в `Review/<kb_id>/<proposal_id>.md`.
- **Выход:** тесты §19.2 доказывают, что близость по времени не объединяет; отказ LLM не ломает публикацию.

### Этап 6. Защита редактирования

- `reconcile`: инкрементальный + полный, `observations`, перемещения, missing, дубли `kb_id`.
- Конфликты → заморозка автоматических изменений затронутых файлов.
- Применение предложений: проверка base_etag/base_sha256, сохранение предыдущей версии, отказ при расхождении.
- **Выход:** тесты §19.4 (переименование, перемещение, удаление `kb_id`, дубль, ручная правка перед заменой).

### Этап 7. Эксплуатация

- `status`/`doctor` с реальным прогрессом, метрики §16.
- `backup` (SQLite `.backup`/`VACUUM INTO` + архив + конфиг), `restore` только явно.
- Восстановление в чистом окружении (тест), README для Windows и Ubuntu, инструкции по S3/Obsidian.
- **Выход:** критерии приёмки MVP §20 — по пунктам, с отметкой «проверено/на моках/не проверено».

---

## 10. Риски и ловушки (по приоритету)

| Риск | Последствие | Смягчение |
|---|---|---|
| Неизвестная фактическая структура HTML-экспорта | парсер придётся переделывать | Этап 1 до кода; fixtures из реального экспорта |
| Провайдер S3 не поддерживает условные записи | невозможно безопасное авто-применение | явное ручное применение (ADR 0006), не overwrite |
| Шифрование плагина Obsidian несовместимо с plain Markdown | вся публикация нечитаема | выяснить формат до Этапа 3 |
| Python 3.15 без wheels | сборка падает на Windows | перейти на 3.13 (см. §4) |
| Правки пользователя и `kb_id` в YAML | `frontmatter` может переформатировать файл | читаем/пишем front matter только для новых файлов; для существующих — read-only |
| Альбом без события «завершён» | склейка по времени | окно ожидания + `media_group_id`, поздний элемент → `Review/` |
| ETag ≠ hash | ложное «не изменилось» | ETag только как подсказка, sha256 при сомнении |
| Лимит Bot API на скачивание (20 МБ) | часть вложений недоступна | статус `oversized`, явная отметка в заметке |
| Кириллица/emoji в путях S3 и Windows | коллизии, обрезка | slugify+короткий суффикс, `pathvalidate`, тест длины |
| LLM-инъекции в текстах сообщений | утечка/команды | сообщения = данные; промпт не исполняет инструкции из текста |

---

## 11. Что нужно от вас сейчас

1. **Подтвердить Python 3.13** и разрешить пересоздать `.venv` (сейчас 3.15.2).
2. **S3**: провайдер, endpoint, bucket, имена префиксов (или доступ к тестовому префиксу для S1).
3. **Плагин Obsidian** для синхронизации — название и версия.
4. **LLM**: провайдер/модель и допустимость передачи текста сообщений внешнему API.
5. **HTML-экспорт**: путь к папке экспорта (или обезличенный фрагмент) — без него парсер пишется «по догадкам».
6. **Рубрикатор**: черновой список тем или разрешение начать с примера и `Inbox`.

После ответов — Этап 0 и Этап 3 (вертикальный срез), а спайки S1–S5 идут параллельно по мере появления доступов.
