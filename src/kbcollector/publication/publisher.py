"""Подготовка вложений и публикация заметки с соблюдением порядка.

Порядок из ТЗ §13.2: сначала вложения, проверка, затем Markdown со ссылками.
Запись выполняется через `If-None-Match: *`, поэтому повторная публикация
не перезаписывает существующий объект (ADR 0002).
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from kbcollector.archive.raw_store import sha256_file
from kbcollector.domain.ids import attachment_id
from kbcollector.domain.message import AttachmentRef
from kbcollector.domain.paths import attachment_filename
from kbcollector.ingestion.html_export.discovery import resolve_attachment
from kbcollector.publication.layout import VaultLayout
from kbcollector.publication.s3_client import ObjectStat, S3Store

MARKDOWN_CONTENT_TYPE = "text/markdown; charset=utf-8"
DEFAULT_CONTENT_TYPE = "application/octet-stream"

STATUS_READY = "ready"
STATUS_UPLOADED = "uploaded"
STATUS_EXISTING = "existing"
STATUS_MISSING = "missing"
STATUS_NOT_INCLUDED = "not_included"


@dataclass(frozen=True, slots=True)
class PreparedAttachment:
    """Вложение, готовое к публикации (или честно помеченное как недоступное)."""

    ref: AttachmentRef
    attachment_id: str
    filename: str
    status: str
    source: Path | None = None
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    note_key: str
    created: bool
    uploaded: int
    existing: int
    note_stat: ObjectStat | None


def prepare_attachment(
    export_root: Path,
    *,
    chat_id: int,
    message_id: int,
    ref: AttachmentRef,
) -> PreparedAttachment:
    """Проверяет наличие файла и считает хеш, ничего не записывая."""
    identifier = attachment_id(chat_id, message_id, ref.rel_path or ref.original_name)
    filename = attachment_filename(ref.original_name)

    if not ref.included or ref.rel_path is None:
        return PreparedAttachment(
            ref=ref,
            attachment_id=identifier,
            filename=filename,
            status=STATUS_NOT_INCLUDED,
        )

    path = resolve_attachment(export_root, ref.rel_path)
    if path is None:
        return PreparedAttachment(
            ref=ref,
            attachment_id=identifier,
            filename=filename,
            status=STATUS_MISSING,
        )

    return PreparedAttachment(
        ref=ref,
        attachment_id=identifier,
        filename=filename,
        status=STATUS_READY,
        source=path,
        size=path.stat().st_size,
        sha256=sha256_file(path),
    )


def _content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or DEFAULT_CONTENT_TYPE


def publish_note(
    *,
    note_key: str,
    note_text: str,
    attachments: list[PreparedAttachment],
    layout: VaultLayout,
    store: S3Store,
) -> PublishOutcome:
    """Загружает вложения, затем заметку. Существующие объекты не перезаписываются."""
    uploaded = 0
    existing = 0

    for item in attachments:
        if item.status != STATUS_READY or item.source is None:
            continue
        key = layout.attachment_key(item.attachment_id, item.filename)
        body = item.source.read_bytes()
        stat = store.put_if_absent(key, body, _content_type(item.filename))
        if stat is None:
            existing += 1
        else:
            uploaded += 1

    note_stat = store.put_if_absent(note_key, note_text.encode("utf-8"), MARKDOWN_CONTENT_TYPE)
    return PublishOutcome(
        note_key=note_key,
        created=note_stat is not None,
        uploaded=uploaded,
        existing=existing,
        note_stat=note_stat,
    )
