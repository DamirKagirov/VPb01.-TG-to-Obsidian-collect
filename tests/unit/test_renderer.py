"""Рендеринг заметки: секции, ссылки, отсутствующие вложения."""

from __future__ import annotations

from datetime import UTC, datetime

from kbcollector.domain.message import AttachmentRef, ForwardInfo, NoteDraft
from kbcollector.markdown.renderer import AttachmentView, render_note

CREATED = datetime(2026, 1, 1, 7, 0, tzinfo=UTC)


def make_draft(**overrides: object) -> NoteDraft:
    values: dict[str, object] = {
        "kb_id": "note_abc",
        "title": "Заголовок",
        "category": "Inbox",
        "created_at": CREATED,
        "source_kind": "html_export",
        "chat_id": 42,
        "message_id": 100,
        "text": "Текст сообщения",
    }
    values.update(overrides)
    return NoteDraft(**values)  # type: ignore[arg-type]


def _frontmatter(note: str) -> dict[str, object]:
    import yaml

    block = note.split("---\n", 2)[1]
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict)
    return parsed


def test_required_sections_and_frontmatter() -> None:
    note = render_note(make_draft(), [])

    assert note.startswith("---\n")
    assert 'kb_id: "note_abc"' in note
    assert "# Заголовок" in note
    assert "## Материалы" in note
    assert "### Сообщение 100" in note
    assert "## Источники" in note
    assert "Чат `42`" in note


def test_frontmatter_keeps_only_identity_and_date() -> None:
    frontmatter = _frontmatter(render_note(make_draft(), []))

    assert set(frontmatter) == {"kb_id", "created_at"}
    assert frontmatter["created_at"] == "2026-01-01T07:00:00Z"


def test_tags_appear_only_when_present() -> None:
    without_tags = _frontmatter(render_note(make_draft(), []))
    with_tags = _frontmatter(render_note(make_draft(tags=("python",)), []))

    assert "tags" not in without_tags
    assert with_tags["tags"] == ["python"]


def test_no_empty_attachments_section() -> None:
    note = render_note(make_draft(), [])

    assert "## Вложения" not in note


def test_attachment_link_and_missing_marker() -> None:
    draft = make_draft(
        attachments=(
            AttachmentRef(kind="photo", original_name="photo.jpg", rel_path="photos/photo.jpg"),
            AttachmentRef(kind="document", original_name="missing.docx", rel_path=None),
        )
    )
    views = [
        AttachmentView(
            rel_path="photos/photo.jpg",
            original_name="photo.jpg",
            link="../../Attachments/photo.jpg",
            size_hint="644×130",
        ),
        AttachmentView(
            rel_path=None,
            original_name="missing.docx",
            link=None,
            problem="файл не включён в экспорт Telegram",
        ),
    ]

    note = render_note(draft, views)

    assert "[photo.jpg](../../Attachments/photo.jpg) — 644×130" in note
    assert "`missing.docx`" in note
    assert "файл не включён в экспорт Telegram" in note


def test_attachment_link_with_spaces_and_parentheses_is_wrapped() -> None:
    draft = make_draft()
    views = [
        AttachmentView(
            rel_path="stickers/AnimatedSticker (2).tgs",
            original_name="AnimatedSticker (2).tgs",
            link="../../Attachments/Telegram/att_x/AnimatedSticker (2).tgs",
        )
    ]

    note = render_note(draft, views)

    assert "](" + "<../../Attachments/Telegram/att_x/AnimatedSticker (2).tgs>" + ")" in note


def test_inline_attachment_token_is_replaced() -> None:
    draft = make_draft(text="Смотри {{att:photos/a.jpg}} тут")
    views = [
        AttachmentView(
            rel_path="photos/a.jpg",
            original_name="a.jpg",
            link="../../Attachments/a.jpg",
        )
    ]

    note = render_note(draft, views)

    assert "{{att:" not in note
    assert "[a.jpg](../../Attachments/a.jpg)" in note


def test_forward_and_reply_are_reported() -> None:
    draft = make_draft(
        reply_to_id=99,
        forward=ForwardInfo(
            author="Автор Оригинала",
            sent_at=datetime(2025, 12, 15, 6, 30, tzinfo=UTC),
        ),
    )

    note = render_note(draft, [])

    assert "Переслано из: Автор Оригинала" in note
    assert "Ответ на сообщение `99`" in note


def test_empty_text_message_is_explicit() -> None:
    note = render_note(make_draft(text=""), [])

    assert "_Пустое сообщение._" in note
