"""Идентификаторы: стабильность и детерминированность."""

from __future__ import annotations

from kbcollector.domain.ids import (
    attachment_id,
    kb_id_for,
    new_kb_id,
    new_uuid,
    short_suffix,
)


def test_kb_id_is_stable_for_same_source_message() -> None:
    first = kb_id_for("html_export", 100000001, 123456)
    second = kb_id_for("html_export", 100000001, 123456)

    assert first == second
    assert first.startswith("note_")


def test_kb_id_differs_between_sources_and_messages() -> None:
    base = kb_id_for("html_export", 1, 10)

    assert base != kb_id_for("html_export", 1, 11)
    assert base != kb_id_for("html_export", 2, 10)
    assert base != kb_id_for("telegram_live", 1, 10)


def test_attachment_id_is_deterministic() -> None:
    first = attachment_id(1, 2, "photos/a.jpg")
    second = attachment_id(1, 2, "photos/a.jpg")

    assert first == second
    assert first.startswith("att_")
    assert first != attachment_id(1, 2, "photos/b.jpg")


def test_short_suffix_has_fixed_length() -> None:
    kb_id = new_kb_id()

    suffix = short_suffix(kb_id)

    assert len(suffix) == 6
    assert suffix.isalnum()


def test_new_uuid_is_unique() -> None:
    values = {new_uuid() for _ in range(50)}

    assert len(values) == 50
