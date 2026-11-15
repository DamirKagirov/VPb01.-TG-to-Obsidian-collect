"""Тесты предстартовых проверок."""

from __future__ import annotations

from pathlib import Path

from kbcollector.config import Settings
from kbcollector.diagnostics.preflight import Check, Severity, has_failures, run_preflight


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def check_by_name(settings: Settings, name: str) -> Check:
    return next(check for check in run_preflight(settings) if check.name == name)


def test_all_checks_have_unique_names(tmp_path: Path) -> None:
    checks = run_preflight(make_settings(data_dir=tmp_path / "data"))

    names = [check.name for check in checks]
    assert len(names) == len(set(names))


def test_unconfigured_services_warn_but_do_not_fail(tmp_path: Path) -> None:
    checks = run_preflight(make_settings(data_dir=tmp_path / "data", telegram_bot_token=None))

    assert has_failures(checks) is False
    severities = {check.name: check.severity for check in checks}
    assert severities["telegram"] is Severity.WARN
    assert severities["s3"] is Severity.WARN
    assert severities["llm"] is Severity.WARN


def test_empty_allowlist_is_warning_even_with_token(tmp_path: Path) -> None:
    settings = make_settings(
        data_dir=tmp_path / "data",
        telegram_bot_token="123456789:AAH-secret-token-value-1234567890",
    )

    telegram = check_by_name(settings, "telegram")

    assert telegram.severity is Severity.WARN
    assert "allowlist" in telegram.detail


def test_telegram_ok_with_token_and_allowlist(tmp_path: Path) -> None:
    settings = make_settings(
        data_dir=tmp_path / "data",
        telegram_bot_token="123456789:AAH-secret-token-value-1234567890",
        telegram_allowed_user_ids="42",
    )

    checks = run_preflight(settings)
    telegram = next(check for check in checks if check.name == "telegram")

    assert telegram.severity is Severity.OK
    assert has_failures(checks) is False


def test_data_dir_is_created_and_reported(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "state"
    settings = make_settings(data_dir=target)

    check_by_name(settings, "data_dir")

    assert target.is_dir()


def test_unwritable_data_dir_fails(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("occupied", encoding="utf-8")
    settings = make_settings(data_dir=blocker / "child")

    check = check_by_name(settings, "data_dir")

    assert check.severity is Severity.FAIL
    assert has_failures([check]) is True


def test_s3_check_reports_prefixes(tmp_path: Path) -> None:
    settings = make_settings(data_dir=tmp_path / "data", s3_bucket="kb-bucket")

    check = check_by_name(settings, "s3")

    assert check.severity is Severity.OK
    assert "vault/" in check.detail
