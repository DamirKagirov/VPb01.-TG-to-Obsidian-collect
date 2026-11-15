"""Общие фикстуры тестов."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_workdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Все тесты работают во временном каталоге.

    Это гарантирует, что тесты не прочитают случайный `.env` разработчика
    и не создадут `data/` в репозитории.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KBC_LOG_LEVEL", raising=False)
    monkeypatch.delenv("KBC_LOG_JSON", raising=False)
