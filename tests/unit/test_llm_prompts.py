"""Промпты: секции, подстановка, устойчивость к фигурным скобкам."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kbcollector.llm.prompts import PromptError, load_prompt, render_user

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = PROJECT_ROOT / "config" / "prompts" / "enrich.v1.md"


def test_project_prompt_loads() -> None:
    prompt = load_prompt(PROMPT_PATH, version="v1")

    assert prompt.version == "v1"
    assert "JSON" in prompt.system
    assert "{categories}" in prompt.user_template


def test_render_substitutes_values_and_keeps_braces() -> None:
    prompt = load_prompt(PROMPT_PATH, version="v1")

    rendered = render_user(
        prompt,
        categories=("Inbox", "n8n"),
        sent_at=datetime(2026, 11, 16, 13, 14, tzinfo=UTC),
        forward_author="Автор",
        text="конфиг {shared} и {{x}}",
    )

    assert "Inbox, n8n" in rendered
    assert "2026-11-16 13:14" in rendered
    assert "Переслано из: Автор" in rendered
    assert "конфиг {shared} и {{x}}" in rendered


def test_no_forward_block_when_author_absent() -> None:
    prompt = load_prompt(PROMPT_PATH, version="v1")

    rendered = render_user(
        prompt,
        categories=("Inbox",),
        sent_at=datetime(2026, 1, 1, tzinfo=UTC),
        forward_author=None,
        text="текст",
    )

    assert "Переслано из" not in rendered


def test_missing_sections_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text("только текст", encoding="utf-8")

    with pytest.raises(PromptError):
        load_prompt(path, version="v1")
