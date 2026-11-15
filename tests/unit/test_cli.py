"""Тесты CLI: контракт команд, коды возврата, отсутствие «фальшивого успеха»."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from kbcollector import __version__
from kbcollector.cli.app import app

runner = CliRunner()


def test_version_prints_application_and_python() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout
    assert "Python" in result.stdout


def test_help_lists_implemented_commands_only() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "doctor" in result.stdout
    assert "version" in result.stdout
    assert "import-html" in result.stdout
    # Команды следующих этапов ещё не существуют — заглушек быть не должно.
    assert "reconcile" not in result.stdout


def test_doctor_succeeds_with_warnings_only() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "data_dir" in result.stdout
    assert "sqlite" in result.stdout
    assert "telegram" in result.stdout


def test_doctor_json_is_machine_readable() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    names = {item["name"] for item in payload}
    assert {"python", "sqlite", "data_dir", "telegram", "s3", "llm"} <= names
    assert all(item["severity"] in {"ok", "warn", "fail"} for item in payload)


def test_doctor_quiet_prints_nothing() -> None:
    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_invalid_configuration_exits_with_code_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KBC_LOG_LEVEL", "LOUD")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 2
    assert "KBC_LOG_LEVEL" in result.stdout
