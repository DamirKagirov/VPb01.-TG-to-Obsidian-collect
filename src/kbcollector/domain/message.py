"""Доменные модели сообщений и заметок (без I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

AttachmentKind = Literal["photo", "video", "document", "sticker", "audio", "other"]

# Источник: импорт HTML-экспорта. Используется в идентичности сообщений и заметок.
SOURCE_HTML_EXPORT = "html_export"

# Маркер вложения в тексте: заменяется на Markdown-ссылку после загрузки.
ATTACHMENT_TOKEN = "{{att:{rel_path}}}"


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Ссылка на вложение из разметки экспорта.

    `rel_path` равен `None`, если файл не включён в экспорт (Telegram показывает
    заглушку «Not included, change data exporting settings to download»).
    """

    kind: AttachmentKind
    original_name: str
    rel_path: str | None = None
    size_hint: str | None = None
    included: bool = True


@dataclass(frozen=True, slots=True)
class ForwardInfo:
    """Сведения о пересылке: автор оригинала и дата оригинала (ТЗ §8.2)."""

    author: str | None
    sent_at: datetime | None


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    """Сообщение, извлечённое из HTML-экспорта."""

    message_id: int
    sent_at: datetime
    author: str | None
    text: str
    joined: bool = False
    forward: ForwardInfo | None = None
    reply_to_id: int | None = None
    attachments: tuple[AttachmentRef, ...] = ()
    source_page: str | None = None
    raw_payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NoteDraft:
    """Заметка до публикации."""

    kb_id: str
    title: str
    category: str
    created_at: datetime
    source_kind: str
    chat_id: int
    message_id: int
    text: str
    attachments: tuple[AttachmentRef, ...] = ()
    forward: ForwardInfo | None = None
    reply_to_id: int | None = None
    tags: tuple[str, ...] = ()
