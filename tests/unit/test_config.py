"""Тесты настроек: приоритет источников, валидация, производные пути."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from kbcollector.config import Settings


def make_settings(**overrides: object) -> Settings:
    """Настройки без чтения `.env` — тесты не зависят от машины разработчика."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("KBC_S3_BUCKET", "KBC_TELEGRAM_BOT_TOKEN", "KBC_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    settings = make_settings()

    assert settings.telegram_configured is False
    assert settings.s3_configured is False
    assert settings.llm_configured is False
    assert settings.telegram_allowed_user_ids == []
    assert settings.s3_vault_prefix == "vault/"
    assert settings.timezone == "UTC"


def test_derived_paths_live_under_data_dir() -> None:
    settings = make_settings(data_dir="var/state")

    assert settings.db_path.as_posix() == "var/state/kbcollector.sqlite3"
    assert settings.raw_archive_dir.as_posix() == "var/state/archive"
    assert settings.staging_dir.as_posix() == "var/state/staging"


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KBC_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("KBC_S3_VAULT_PREFIX", "/vault")
    monkeypatch.setenv("KBC_LOG_LEVEL", "debug")

    settings = make_settings()

    assert settings.s3_bucket == "my-bucket"
    assert settings.s3_vault_prefix == "vault/"
    assert settings.log_level == "DEBUG"


def test_prefix_without_slash_is_normalized() -> None:
    settings = make_settings(s3_archive_prefix="archive/telegram")

    assert settings.s3_archive_prefix == "archive/telegram/"


@pytest.mark.parametrize("value", ["", "   ", "/"])
def test_empty_prefix_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(s3_vault_prefix=value)


def test_allowlist_accepts_csv() -> None:
    settings = make_settings(
        telegram_allowed_user_ids="111, 222,333",
        telegram_allowed_chat_ids="-1001234567890",
    )

    assert settings.telegram_allowed_user_ids == [111, 222, 333]
    assert settings.telegram_allowed_chat_ids == [-1001234567890]


def test_allowlist_accepts_json_array() -> None:
    settings = make_settings(telegram_allowed_user_ids='["7", "8"]')

    assert settings.telegram_allowed_user_ids == [7, 8]


def test_empty_allowlist_env_is_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KBC_TELEGRAM_ALLOWED_USER_IDS", "")

    assert make_settings().telegram_allowed_user_ids == []


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(log_level="LOUD")


def test_secrets_are_not_exposed_in_repr() -> None:
    settings = make_settings(
        telegram_bot_token="123456789:AAH-secret-token-value",
        s3_secret_access_key="top-secret",
        llm_api_key="sk-secret",
    )

    rendered = repr(settings)

    assert "AAH-secret-token-value" not in rendered
    assert "top-secret" not in rendered
    assert "sk-secret" not in rendered
    assert settings.telegram_bot_token is not None
    assert settings.telegram_bot_token.get_secret_value().endswith("AAH-secret-token-value")


def test_settings_are_immutable() -> None:
    settings = make_settings()

    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]


def test_empty_env_values_mean_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "KBC_S3_BUCKET",
        "KBC_S3_ENDPOINT_URL",
        "KBC_LLM_BASE_URL",
        "KBC_LLM_MODEL",
        "KBC_TELEGRAM_BOT_TOKEN",
    ):
        monkeypatch.setenv(name, "")

    settings = make_settings()

    assert settings.s3_bucket is None
    assert settings.s3_endpoint_url is None
    assert settings.llm_base_url is None
    assert settings.llm_model is None
    assert settings.telegram_bot_token is None
    assert settings.s3_configured is False
    assert settings.llm_configured is False


def test_html_import_requires_directory_and_chat_id(tmp_path: Path) -> None:
    assert make_settings().html_import_configured is False

    settings = make_settings(html_export_dir=tmp_path, html_chat_id=366097491)

    assert settings.html_import_configured is True
    assert settings.html_chat_id == 366097491
