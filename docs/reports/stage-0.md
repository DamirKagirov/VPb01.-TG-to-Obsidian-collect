# Отчёт по этапу 0 — каркас проекта

Дата: 2026-11-15. Основание: `prompt.md` §21 (Этап 1 частично), §18, §22.

## Что реализовано

| Область | Содержание |
|---|---|
| Окружение | `.venv` пересоздан на Python **3.13.7** (было 3.15.2), менеджер зависимостей — `uv` |
| Зависимости | 19 runtime + 12 dev, `uv.lock` зафиксирован и коммитится |
| Конфигурация | `pydantic-settings`, префикс `KBC_`, секреты как `SecretStr`, allowlist из CSV/JSON, нормализация S3-префиксов |
| Логирование | `structlog` в stderr, JSON или console renderer, маскирование bot-token и полей `*_TOKEN/*_SECRET*/*_API_KEY`, обрезка текстов сообщений |
| CLI | `version`, `doctor` (`--json`, `--quiet`), глобальный `--log-level`, коды возврата 0/1/2 |
| Диагностика | 7 проверок без обращения к сети: Python, data_dir, SQLite (версия + WAL), timezone, Telegram, S3, LLM |
| Docker | `Dockerfile` (uv, non-root uid 10001, volume `/data`), `compose.yaml` (init, healthcheck, stop_grace_period), `.dockerignore` |
| Качество | `ruff` (lint + format), `mypy --strict` на `src`, `pre-commit` с локальными хуками через `uv run` |
| Тесты | 32 unit-теста: конфигурация, маскирование логов, preflight, CLI |

## Файлы

```
pyproject.toml  uv.lock  .env.example  .pre-commit-config.yaml
Dockerfile  compose.yaml  .dockerignore  README.md
src/kbcollector/__init__.py  __main__.py  config.py  logging.py  py.typed
src/kbcollector/cli/{__init__.py, app.py}
src/kbcollector/diagnostics/{__init__.py, preflight.py}
tests/conftest.py  tests/unit/{test_config,test_logging,test_preflight,test_cli}.py
docs/reports/stage-0.md
```

## Как запустить

```powershell
.\.venv\Scripts\uv.exe sync
.\.venv\Scripts\kbcollector.exe doctor
.\.venv\Scripts\uv.exe run pytest -q
.\.venv\Scripts\uv.exe run ruff check .
.\.venv\Scripts\uv.exe run mypy
```

## Выполненные проверки

| Проверка | Результат |
|---|---|
| `ruff check .` | без замечаний |
| `ruff format --check .` | 16 файлов отформатированы |
| `mypy` (strict, src) | без ошибок, 8 файлов |
| `pytest -q` | **32 passed** |
| `kbcollector doctor` | python/data_dir/sqlite/timezone — ok; telegram/s3/llm — warn (не настроены) |
| `uv.lock` не игнорируется git | подтверждено `git check-ignore` |
| `compose.yaml` | разбирается как YAML; семантика Compose **не проверена** |

## Что проверено только статически / не проверено

- **Docker**: в окружении разработки `docker` не установлен. `Dockerfile` и `compose.yaml`
  не собирались и не запускались. Статус: **не проверено**, требуется проверка на Ubuntu/Docker Desktop.
- **Внешние сервисы** (Telegram, S3, LLM): не вызывались и не настраивались — намеренно,
  `doctor` работает офлайн.
- **Совместимость S3 ↔ Obsidian**: не начата (Этап 2).

## Известные ограничения этапа

- Реализованы только `version` и `doctor`; остальные команды §16 отсутствуют — заглушек нет.
- Нет БД, миграций Alembic и моделей: появятся на Этапе 3 вместе с первыми сущностями.
- `compose.yaml` использует команду `doctor`; на Этапе 3 она станет `run`.
- Кириллица в консоли Windows зависит от кодовой страницы терминала
  (`chcp 65001` или Windows Terminal); в файл и в Docker (UTF-8) выводится корректно.

## Решения, требующие подтверждения

1. S3: провайдер, endpoint, bucket, имена префиксов (`vault/`, `archive/`, `backup/` — заготовки).
2. Плагин синхронизации Obsidian (для спайка S1).
3. LLM: провайдер, модель, допустимость передачи текста сообщений.
4. Telegram: allowlist (числовые ID), текущий обработчик бота (webhook?).
5. Рубрикатор: черновой список тем или старт с `Inbox`.

## Следующий шаг

Этап 1: разбор `messages.html` (838 сообщений, 758 блоков даты, 172 продолжения `joined`)
и фиксация фактической структуры в `docs/import.md`, затем fixtures для парсера.
