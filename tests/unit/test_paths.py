"""Безопасные имена файлов: Windows-ограничения и кириллица."""

from __future__ import annotations

import pytest

from kbcollector.domain.ids import kb_id_for
from kbcollector.domain.paths import (
    MAX_FILENAME_LENGTH,
    attachment_filename,
    note_filename,
    slugify_title,
)

FORBIDDEN = set('<>:"/\\|?*')


@pytest.mark.parametrize(
    "title",
    [
        "Привет: мир?",
        'кавычки "и" апостроф',
        "a/b\\c",
        "заголовок с * звёздочкой",
        "точка в конце.",
    ],
)
def test_note_filename_has_no_forbidden_characters(title: str) -> None:
    kb_id = kb_id_for("html_export", 1, 1)

    name = note_filename(title, kb_id)

    assert not (set(name) & (FORBIDDEN - {"."}))
    assert name.endswith(".md")
    assert name.endswith(f"-{kb_id[-6:]}.md")


def test_note_filename_keeps_cyrillic() -> None:
    name = note_filename("Полезный материал", kb_id_for("html_export", 1, 2))

    assert "Полезный" in name


def test_long_title_is_truncated() -> None:
    name = note_filename("я" * 400, kb_id_for("html_export", 1, 3))

    assert len(name) <= MAX_FILENAME_LENGTH


def test_reserved_windows_names_are_prefixed() -> None:
    assert slugify_title("CON").lower() != "con"


def test_attachment_filename_strips_path() -> None:
    name = attachment_filename("../../etc/passwd")

    assert "/" not in name
    assert "\\" not in name
    assert not name.startswith("..")


def test_attachment_filename_keeps_extension() -> None:
    assert attachment_filename("Документ тестовый.docx").endswith(".docx")
