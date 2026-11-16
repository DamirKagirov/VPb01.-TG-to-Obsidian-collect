"""Типизированные настройки приложения.

Источники значений (по возрастанию приоритета):
переменные окружения ``KBC_*`` → файл ``.env`` → значения по умолчанию.

Секреты описаны как ``SecretStr``: они не попадают в repr, логи и отчёты.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    """Режим запуска приложения."""

    DEV = "dev"
    PROD = "prod"


class Settings(BaseSettings):
    """Настройки одного процесса kbcollector."""

    model_config = SettingsConfigDict(
        env_prefix="KBC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    env: Environment = Environment.DEV

    # ------------------------------------------------------------ состояние
    data_dir: Path = Path("data")
    timezone: str = "UTC"

    # ------------------------------------------------------------- Telegram
    # Allowlist разбирается собственным валидатором: CSV или JSON, а не только JSON
    # (поэтому NoDecode — pydantic-settings не пытается парсить значение сам).
    telegram_bot_token: SecretStr | None = None
    telegram_allowed_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    telegram_allowed_chat_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    telegram_poll_timeout: int = Field(default=50, ge=1, le=50)

    # ------------------------------------------------------------------- S3
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str | None = None
    s3_vault_prefix: str = "vault/"
    s3_archive_prefix: str = "archive/"
    s3_backup_prefix: str = "backup/"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    # ------------------------------------------------------------------ LLM
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_prompt_version: str = "v1"
    # Отправка текста сообщений внешнему провайдеру выключена по умолчанию.
    llm_send_content: bool = False
    llm_budget_tokens: int = Field(default=700_000, ge=0)
    llm_max_input_chars: int = Field(default=6000, ge=500)
    prompt_dir: Path = Path("config/prompts")

    # ------------------------------------------------------------- Импорт HTML
    # Каталог экспорта Telegram Desktop и chat_id, к которому он относится:
    # в самом HTML chat_id отсутствует (см. ADR 0003).
    html_export_dir: Path | None = None
    html_chat_id: int | None = None

    # ---------------------------------------------------------- Рубрикатор
    rubricator_path: Path = Path("config/rubricator.yaml")

    # ------------------------------------------------------------- Логирование
    log_level: str = "INFO"
    log_json: bool = True

    # ------------------------------------------------------------- Производные
    @property
    def db_path(self) -> Path:
        """Путь к SQLite-базе состояния."""
        return self.data_dir / "kbcollector.sqlite3"

    @property
    def raw_archive_dir(self) -> Path:
        """Неизменяемый архив исходных payload и экспортов."""
        return self.data_dir / "archive"

    @property
    def staging_dir(self) -> Path:
        """Промежуточные файлы (скачанные вложения до публикации)."""
        return self.data_dir / "staging"

    @property
    def telegram_configured(self) -> bool:
        return self.telegram_bot_token is not None

    @property
    def s3_configured(self) -> bool:
        return self.s3_bucket is not None

    @property
    def llm_configured(self) -> bool:
        return self.llm_base_url is not None and self.llm_model is not None

    @property
    def html_import_configured(self) -> bool:
        return self.html_export_dir is not None and self.html_chat_id is not None

    # ------------------------------------------------------------- Валидация
    @field_validator(
        "telegram_bot_token",
        "s3_endpoint_url",
        "s3_bucket",
        "s3_access_key_id",
        "s3_secret_access_key",
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        "html_export_dir",
        "html_chat_id",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        """Пустая переменная в `.env` означает «не задано», а не пустую строку."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("telegram_allowed_user_ids", "telegram_allowed_chat_ids", mode="before")
    @classmethod
    def _parse_id_list(cls, value: object) -> object:
        """Принимает CSV (`1, 2, 3`), JSON-массив или готовый список."""
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            return json.loads(raw)
        return [part for part in (chunk.strip() for chunk in raw.split(",")) if part]

    @field_validator("s3_vault_prefix", "s3_archive_prefix", "s3_backup_prefix")
    @classmethod
    def _require_trailing_slash(cls, value: str) -> str:
        cleaned = value.strip().strip("/")
        if not cleaned:
            raise ValueError("префикс не может быть пустым")
        return f"{cleaned}/"

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        level = value.strip().upper()
        if level not in allowed:
            raise ValueError(f"ожидается один из {sorted(allowed)}")
        return level

    @field_validator("data_dir")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        return value.expanduser()
