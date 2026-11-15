# kbcollector

Конвейер: **Telegram-коллекция → надёжный архив → Markdown → S3 → Obsidian**.

Приложение собирает полезные сообщения (импорт HTML-экспорта Telegram Desktop и live-приём через
Bot API), сохраняет оригиналы, формирует заметки со стабильным `kb_id`, публикует их в
S3-совместимое хранилище и следит за тем, чтобы **правки пользователя в Obsidian никогда не
затирались автоматически**. Повторная обработка порождает предложения в `Review/`, а не
перезапись рабочих заметок.

Полное ТЗ — [`prompt.md`](prompt.md), разбор и план — [`docs/analysis-and-plan.md`](docs/analysis-and-plan.md).

## Статус

| Этап | Содержание | Состояние |
|---|---|---|
| 0 | Каркас: пакет, конфигурация, логи, CLI, Docker, тесты | **выполнен** |
| 1 | Аудит HTML-экспорта, ADR, список открытых вопросов | **выполнен** |
| 2 | Спайки: S3 ↔ Obsidian, условные записи, offset, ID-сопоставление | **выполнен** (Obsidian и live Telegram — не проверены) |
| 3 | Вертикальный срез: исходник → заметка с `kb_id` → S3 | не начат |
| 4 | Пакетный импорт, вложения, очередь и retries | не начат |
| 5 | Рубрикатор, группировка, LLM-обогащение, `Review/` | не начат |
| 6 | Сверка S3, перемещения, `missing`, конфликты `kb_id` | не начат |
| 7 | Эксплуатация: диагностика, backup/restore, README | не начат |

На Этапе 0 реализованы только реально работающие команды. Заглушек, имитирующих готовую
функциональность, в проекте нет: остальные команды появятся вместе с модулями.

## Требования

- Python **3.13** (зафиксировано в `requires-python`; 3.14/3.15 не поддерживаются намеренно —
  часть бинарных зависимостей ещё не имеет wheels);
- [uv](https://docs.astral.sh/uv/) для управления зависимостями (`uv.lock` хранится в Git);
- Docker + Compose — для целевого развёртывания на Ubuntu 24.04.

## Быстрый старт (Windows)

```powershell
# окружение уже создано в .venv на Python 3.13
.\.venv\Scripts\python.exe -m pip install uv     # если uv ещё нет
.\.venv\Scripts\uv.exe sync                      # установит зависимости и сам проект

.\.venv\Scripts\kbcollector.exe version
.\.venv\Scripts\kbcollector.exe doctor           # проверки без обращения к внешним сервисам
.\.venv\Scripts\pytest.exe -q
```

## Быстрый старт (Ubuntu)

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install uv
uv sync
kbcollector doctor
pytest -q
```

## Конфигурация

Скопируйте `.env.example` в `.env` и заполните локально — `.env` в Git не попадает.

```bash
cp .env.example .env
```

Секреты читаются только из окружения/`.env`; в логах токен бота и значения полей
`*_TOKEN`, `*_SECRET*`, `*_API_KEY` маскируются, полный текст сообщений не логируется.

Отсутствие настроек — не ошибка: `doctor` покажет предупреждения, а базовая публикация
работает без LLM по замыслу.

## CLI

| Команда | Назначение |
|---|---|
| `kbcollector version` | версия приложения и Python |
| `kbcollector doctor [--json] [--quiet]` | проверка конфигурации, каталогов, SQLite, часового пояса |

Код возврата `doctor`: `0` — нет ошибок (предупреждения допустимы), `1` — есть проваленные
проверки, `2` — некорректная конфигурация.

Команды следующих этапов (`run`, `import-html`, `status`, `retry`, `reprocess`, `reconcile`,
`proposals`, `restore`, `backup`) появятся вместе с реализацией; их точный синтаксис будет
задокументирован здесь.

## Docker

```bash
docker compose build
docker compose run --rm kbcollector doctor
```

`compose.yaml` уже содержит non-root пользователя, persistent volume `/data`,
`init: true` и `stop_grace_period`; команда по умолчанию на Этапе 0 — `doctor`,
на Этапе 3 она станет `run`.

Пока отладка ведётся **локально на Windows** — Docker для разработки не требуется,
все проверки выполняются через `uv run`. Контейнерная сборка будет проверена на целевой
ВМ Ubuntu.

## Структура

```
src/kbcollector/
├── cli/            # Typer-команды
├── config.py       # pydantic-settings, секреты как SecretStr
├── diagnostics/    # preflight-проверки (без сети)
└── logging.py      # structlog + маскирование секретов
tests/unit/         # конфигурация, логи, CLI, preflight
docs/               # разбор ТЗ, ADR, отчёты о совместимости
config/             # рубрикатор и версионируемые промпты (появятся на Этапе 5)
```

## Разработка

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
pre-commit install      # ruff + mypy на каждом коммите
```
