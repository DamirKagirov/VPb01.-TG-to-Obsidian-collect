"""Предстартовые проверки окружения.

Принципы (ТЗ §16):
* проверки только читают состояние и пишут во временный файл собственного каталога;
* внешние сервисы (Telegram, S3, LLM) не вызываются — иначе диагностика меняла бы
  состояние и зависела бы от сети;
* отсутствие настройки — это предупреждение, а не ошибка: базовая публикация
  работает без LLM.
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kbcollector.config import Settings

REQUIRED_PYTHON: tuple[int, int] = (3, 13)
MIN_SQLITE: tuple[int, int, int] = (3, 35, 0)


class Severity(StrEnum):
    """Значимость результата проверки."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class Check:
    """Результат одной проверки."""

    name: str
    severity: Severity
    detail: str
    hint: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ"):
        if size < 1024 or unit == "ТиБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТиБ"


def _check_python() -> Check:
    version = sys.version_info
    rendered = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) == REQUIRED_PYTHON:
        return Check("python", Severity.OK, f"Python {rendered}")
    return Check(
        "python",
        Severity.FAIL,
        f"Python {rendered}",
        hint="нужен Python 3.13 — см. requires-python в pyproject.toml",
    )


def _check_data_dir(settings: Settings) -> Check:
    target = settings.data_dir
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=target):
            pass
    except OSError as exc:
        return Check(
            "data_dir",
            Severity.FAIL,
            f"{target}: {exc.strerror or exc}",
            hint="укажите writable-каталог в KBC_DATA_DIR",
        )
    free = shutil.disk_usage(target).free
    return Check("data_dir", Severity.OK, f"{target.resolve()} (свободно {_human_bytes(free)})")


def _check_sqlite() -> Check:
    version = sqlite3.sqlite_version_info
    rendered = sqlite3.sqlite_version
    if version < MIN_SQLITE:
        return Check(
            "sqlite",
            Severity.FAIL,
            f"SQLite {rendered}",
            hint="нужен SQLite 3.35+ (UPDATE ... RETURNING, JSON1)",
        )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.sqlite3"
            # closing() обязателен: sqlite3-контекст делает commit, но не close,
            # а на Windows открытый файл не даст удалить временный каталог.
            with contextlib.closing(sqlite3.connect(probe)) as connection:
                row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
    except sqlite3.Error as exc:
        return Check("sqlite", Severity.FAIL, f"не удалось открыть пробную БД: {exc}")
    mode = str(row[0]).lower() if row else "unknown"
    if mode != "wal":
        return Check(
            "sqlite",
            Severity.WARN,
            f"SQLite {rendered}, journal_mode={mode}",
            hint="WAL недоступен: параллельное чтение будет ограничено",
        )
    return Check("sqlite", Severity.OK, f"SQLite {rendered}, journal_mode=WAL")


def _check_timezone(settings: Settings) -> Check:
    try:
        ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        return Check(
            "timezone",
            Severity.WARN,
            f"{settings.timezone}: база часовых поясов не найдена",
            hint="на Windows нужен пакет tzdata; либо укажите UTC",
        )
    return Check("timezone", Severity.OK, f"{settings.timezone} доступен")


def _check_telegram(settings: Settings) -> Check:
    if not settings.telegram_configured:
        return Check(
            "telegram",
            Severity.WARN,
            "токен не задан — приём сообщений отключён",
            hint="KBC_TELEGRAM_BOT_TOKEN в .env",
        )
    users = len(settings.telegram_allowed_user_ids)
    chats = len(settings.telegram_allowed_chat_ids)
    if users == 0 and chats == 0:
        return Check(
            "telegram",
            Severity.WARN,
            "токен задан, allowlist пуст — все отправители будут отклонены",
            hint="KBC_TELEGRAM_ALLOWED_USER_IDS / KBC_TELEGRAM_ALLOWED_CHAT_IDS",
        )
    return Check(
        "telegram",
        Severity.OK,
        f"токен задан, allowlist: пользователей {users}, чатов {chats}",
    )


def _check_s3(settings: Settings) -> Check:
    if not settings.s3_configured:
        return Check(
            "s3",
            Severity.WARN,
            "bucket не задан — публикация отключена",
            hint="KBC_S3_BUCKET, при необходимости KBC_S3_ENDPOINT_URL",
        )
    endpoint = settings.s3_endpoint_url or "AWS S3"
    credentials = (
        "ключи из окружения"
        if settings.s3_access_key_id is not None
        else "цепочка boto3 по умолчанию"
    )
    return Check(
        "s3",
        Severity.OK,
        f"bucket={settings.s3_bucket}, endpoint={endpoint}, "
        f"vault={settings.s3_vault_prefix}, креды: {credentials}",
    )


def _check_llm(settings: Settings) -> Check:
    if not settings.llm_configured:
        return Check(
            "llm",
            Severity.WARN,
            "не настроен — публикуется базовая заметка без обогащения",
            hint="KBC_LLM_BASE_URL и KBC_LLM_MODEL",
        )
    return Check("llm", Severity.OK, f"model={settings.llm_model}, base_url задан")


def run_preflight(settings: Settings) -> list[Check]:
    """Возвращает полный набор проверок в порядке отображения."""
    return [
        _check_python(),
        _check_data_dir(settings),
        _check_sqlite(),
        _check_timezone(settings),
        _check_telegram(settings),
        _check_s3(settings),
        _check_llm(settings),
    ]


def has_failures(checks: list[Check]) -> bool:
    return any(check.severity is Severity.FAIL for check in checks)
