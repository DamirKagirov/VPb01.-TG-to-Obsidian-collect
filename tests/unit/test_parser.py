"""Разбор HTML-экспорта на обезличенных fixtures."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kbcollector.ingestion.html_export.discovery import (
    ExportLayoutError,
    discover_export,
    resolve_attachment,
)
from kbcollector.ingestion.html_export.parser import derive_title, is_bot_command, iter_messages

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "html"
MSK = timezone(timedelta(hours=3))


def prepare_export(tmp_path: Path, fixture: str = "sample.html") -> Path:
    root = tmp_path / "export"
    root.mkdir()
    shutil.copyfile(FIXTURES / fixture, root / "messages.html")
    return root


def test_service_messages_are_skipped(tmp_path: Path) -> None:
    layout = discover_export(prepare_export(tmp_path))

    messages = list(iter_messages(layout))

    assert [message.message_id for message in messages] == [100, 101, 102, 103, 104]


def test_dates_use_title_attribute_with_timezone(tmp_path: Path) -> None:
    layout = discover_export(prepare_export(tmp_path))
    first = next(iter(iter_messages(layout)))

    assert first.sent_at == datetime(2026, 1, 1, 10, 0, tzinfo=MSK)
    assert first.sent_at.utcoffset() == timedelta(hours=3)


def test_text_formatting(tmp_path: Path) -> None:
    layout = discover_export(prepare_export(tmp_path))
    first = next(iter(iter_messages(layout)))

    text = first.text
    assert "Первая строка\nВторая с [ссылкой](https://example.org/page) и `код`" in text
    assert "```\na = 1\nb = 2\n```" in text
    assert "> цитата\n> вторая" in text
    assert "||секрет||" in text


def test_joined_continuation_is_separate_message_without_author(tmp_path: Path) -> None:
    layout = discover_export(prepare_export(tmp_path))
    messages = list(iter_messages(layout))
    continuation = messages[1]

    assert continuation.message_id == 101
    assert continuation.joined is True
    assert continuation.author is None
    assert continuation.text == "Продолжение без автора"


def test_reply_target_is_preserved(tmp_path: Path) -> None:
    layout = discover_export(prepare_export(tmp_path))
    messages = list(iter_messages(layout))

    assert messages[2].reply_to_id == 100


def test_forward_author_and_date_are_distinguished(tmp_path: Path) -> None:
    layout = discover_export(prepare_export(tmp_path))
    messages = list(iter_messages(layout))
    forwarded = messages[3]

    assert forwarded.author == "Тестовый Пользователь"
    assert forwarded.forward is not None
    assert forwarded.forward.author == "Автор Оригинала"
    assert forwarded.forward.sent_at == datetime(2025, 12, 15, 9, 30, tzinfo=MSK)
    assert forwarded.text == "Текст оригинала с жирным"


def test_bot_command_is_not_a_broken_link(tmp_path: Path) -> None:
    layout = discover_export(prepare_export(tmp_path))
    messages = list(iter_messages(layout))

    assert messages[4].text == "`/start` и обычный текст"


def test_media_references_and_placeholders(tmp_path: Path) -> None:
    layout = discover_export(prepare_export(tmp_path, "media.html"))
    messages = {message.message_id: message for message in iter_messages(layout)}

    photo = messages[200].attachments[0]
    assert photo.kind == "photo"
    assert photo.included is True
    assert photo.rel_path == "photos/photo_1@01-01-2026_11-00-00.jpg"

    document = messages[201].attachments[0]
    assert document.kind == "document"
    assert document.original_name == "Документ тестовый.docx"
    assert document.size_hint == "8.6 KB"

    placeholder = messages[202].attachments[0]
    assert placeholder.included is False
    assert placeholder.rel_path is None
    assert placeholder.kind == "photo"
    assert placeholder.size_hint == "644×130, 8.6 KB"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Простой заголовок", "Простой заголовок"),
        ("[ссылка](https://x) и `код`", "ссылка и код"),
        ("**жирный** текст", "жирный текст"),
    ],
)
def test_derive_title_cleans_markdown(title: str, expected: str) -> None:
    assert derive_title(title, (), 1) == expected


def test_derive_title_truncates_long_lines() -> None:
    title = derive_title("я" * 300, (), 1)

    assert len(title) <= 80
    assert title.endswith("…")


def test_derive_title_falls_back_to_attachment_or_id() -> None:
    from kbcollector.domain.message import AttachmentRef

    ref = AttachmentRef(kind="document", original_name="файл.pdf")

    assert derive_title("", (ref,), 5) == "файл.pdf"
    assert derive_title("", (), 5) == "Сообщение 5"


def test_resolve_attachment_rejects_escapes_and_accepts_files(tmp_path: Path) -> None:
    root = prepare_export(tmp_path)
    (root / "photos").mkdir()
    target = root / "photos" / "ok.jpg"
    target.write_bytes(b"x")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    assert resolve_attachment(root, "photos/ok.jpg") == target.resolve()
    assert resolve_attachment(root, "../secret.txt") is None
    assert resolve_attachment(root, str(tmp_path / "secret.txt")) is None
    assert resolve_attachment(root, "https://example.org/a.jpg") is None
    assert resolve_attachment(root, "photos/absent.jpg") is None


def test_discover_rejects_missing_export(tmp_path: Path) -> None:
    with pytest.raises(ExportLayoutError):
        discover_export(tmp_path / "nope")


def test_discover_finds_nested_export_directory(tmp_path: Path) -> None:
    nested = tmp_path / "ChatExport_2026-11-16"
    nested.mkdir()
    shutil.copyfile(FIXTURES / "sample.html", nested / "messages.html")

    layout = discover_export(tmp_path)

    assert layout.root == nested
    assert layout.page_names == ("messages.html",)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start", True),
        ("  /help  ", True),
        ("/start@example_bot", True),
        ("`/start`", True),
        ("/start и обычный текст", False),
        ("команда /start", False),
        ("", False),
    ],
)
def test_is_bot_command(text: str, expected: bool) -> None:
    assert is_bot_command(text) is expected
