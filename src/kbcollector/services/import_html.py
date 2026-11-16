"""Импорт HTML-экспорта: разбор → архив → БД → публикация.

Вертикальный срез Этапа 3: одно сообщение превращается в одну заметку с `kb_id`,
вложения публикуются до Markdown, повторный импорт не создаёт дублей
(идентичность — `(source_kind, chat_id, message_id)`, `kb_id` детерминирован).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from kbcollector.archive.raw_store import archive_export
from kbcollector.config import Settings
from kbcollector.db.engine import build_engine, build_session_factory
from kbcollector.db.models import (
    Attachment,
    MessageVersion,
    Note,
    Publication,
    RawMessage,
    Source,
)
from kbcollector.domain.ids import kb_id_for, new_uuid
from kbcollector.domain.message import NoteDraft, ParsedMessage
from kbcollector.domain.paths import note_filename
from kbcollector.ingestion.html_export.discovery import ExportLayout, discover_export
from kbcollector.ingestion.html_export.parser import derive_title, is_bot_command, iter_messages
from kbcollector.logging import get_logger
from kbcollector.markdown.renderer import AttachmentView, render_note
from kbcollector.publication.layout import INBOX_CATEGORY, VaultLayout
from kbcollector.publication.publisher import (
    STATUS_MISSING,
    STATUS_NOT_INCLUDED,
    STATUS_READY,
    PreparedAttachment,
    prepare_attachment,
    publish_note,
)
from kbcollector.publication.s3_client import S3Error, S3Store

logger = get_logger(__name__)

SOURCE_KIND = "html_export"
_NOT_INCLUDED_REASON = "файл не включён в экспорт Telegram"
_MISSING_REASON = "файл отсутствует в каталоге экспорта"


class HtmlImportError(RuntimeError):
    """Импорт невозможен из-за конфигурации или структуры экспорта."""


@dataclass(slots=True)
class ImportStats:
    """Результат одного запуска импорта."""

    dry_run: bool
    target_prefix: str
    pages: int = 0
    parsed: int = 0
    notes_created: int = 0
    notes_skipped: int = 0
    notes_already_present: int = 0
    skipped_commands: int = 0
    attachments_uploaded: int = 0
    attachments_existing: int = 0
    attachments_missing: int = 0
    attachments_not_included: int = 0
    failed_messages: int = 0
    failures: list[str] = field(default_factory=list)
    sample_path: Path | None = None
    imported_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NotePlan:
    """Всё, что нужно для публикации одной заметки."""

    draft: NoteDraft
    note_key: str
    attachments: tuple[PreparedAttachment, ...]
    note_text: str


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_plan(
    message: ParsedMessage,
    *,
    chat_id: int,
    export_root: Path,
    layout: VaultLayout,
) -> NotePlan:
    """Готовит заметку без побочных эффектов."""
    kb_id = kb_id_for(SOURCE_KIND, chat_id, message.message_id)
    title = derive_title(message.text, message.attachments, message.message_id)
    filename = note_filename(title, kb_id)
    note_key = layout.note_key(INBOX_CATEGORY, filename)

    prepared = tuple(
        prepare_attachment(
            export_root,
            chat_id=chat_id,
            message_id=message.message_id,
            ref=ref,
        )
        for ref in message.attachments
    )

    views: list[AttachmentView] = []
    for item in prepared:
        if item.status == STATUS_READY:
            link: str | None = layout.link_from_note(
                note_key, layout.attachment_key(item.attachment_id, item.filename)
            )
            problem = None
        else:
            link = None
            problem = (
                _NOT_INCLUDED_REASON if item.status == STATUS_NOT_INCLUDED else _MISSING_REASON
            )
        views.append(
            AttachmentView(
                rel_path=item.ref.rel_path,
                original_name=item.ref.original_name,
                link=link,
                size_hint=item.ref.size_hint,
                kind=item.ref.kind,
                problem=problem,
            )
        )

    draft = NoteDraft(
        kb_id=kb_id,
        title=title,
        category=INBOX_CATEGORY,
        created_at=message.sent_at,
        source_kind=SOURCE_KIND,
        chat_id=chat_id,
        message_id=message.message_id,
        text=message.text,
        attachments=message.attachments,
        forward=message.forward,
        reply_to_id=message.reply_to_id,
    )
    return NotePlan(
        draft=draft,
        note_key=note_key,
        attachments=prepared,
        note_text=render_note(draft, views),
    )


def _note_exists(session: Session, *, chat_id: int, message_id: int) -> bool:
    existing = session.scalar(
        select(Note.kb_id).where(Note.chat_id == chat_id, Note.message_id == message_id)
    )
    return existing is not None


def _store_raw_message(session: Session, message: ParsedMessage, *, chat_id: int) -> RawMessage:
    content_hash = _hash_text(message.text)
    existing = session.scalar(
        select(RawMessage).where(
            RawMessage.source_kind == SOURCE_KIND,
            RawMessage.chat_id == chat_id,
            RawMessage.message_id == message.message_id,
        )
    )
    if existing is not None:
        if existing.content_hash != content_hash:
            existing.content_hash = content_hash
            existing.text = message.text
            session.add(
                MessageVersion(
                    id=new_uuid(),
                    raw_message_id=existing.id,
                    content_hash=content_hash,
                    text=message.text,
                    created_at=datetime.now(UTC),
                )
            )
        return existing

    raw = RawMessage(
        id=new_uuid(),
        source_kind=SOURCE_KIND,
        chat_id=chat_id,
        message_id=message.message_id,
        sent_at=message.sent_at,
        author=message.author,
        forward_author=message.forward.author if message.forward else None,
        forward_sent_at=message.forward.sent_at if message.forward else None,
        reply_to_id=message.reply_to_id,
        is_joined=message.joined,
        text=message.text,
        content_hash=content_hash,
        payload_json=json.dumps(message.raw_payload, ensure_ascii=False),
        created_at=datetime.now(UTC),
    )
    session.add(raw)
    session.flush()
    session.add(
        MessageVersion(
            id=new_uuid(),
            raw_message_id=raw.id,
            content_hash=content_hash,
            text=message.text,
            created_at=datetime.now(UTC),
        )
    )
    return raw


def _store_note(
    session: Session,
    plan: NotePlan,
    *,
    raw: RawMessage,
    note_key: str,
    etag: str | None,
    layout: VaultLayout,
) -> None:
    now = datetime.now(UTC)
    session.add(
        Note(
            kb_id=plan.draft.kb_id,
            created_at=now,
            published_at=now,
            title=plan.draft.title,
            category=plan.draft.category,
            current_path=note_key,
            state="published",
            chat_id=plan.draft.chat_id,
            message_id=plan.draft.message_id,
            sha256=_hash_text(plan.note_text),
            etag=etag,
        )
    )
    for position, item in enumerate(plan.attachments):
        status = "missing"
        if item.status == STATUS_NOT_INCLUDED:
            status = "not_included"
        elif item.status in {STATUS_READY, "uploaded", "existing"}:
            status = "uploaded"
        session.add(
            Attachment(
                id=item.attachment_id,
                raw_message_id=raw.id,
                position=position,
                kind=item.ref.kind,
                original_name=item.ref.original_name,
                rel_path=item.ref.rel_path,
                status=status,
                size=item.size,
                sha256=item.sha256,
                object_key=None
                if status != "uploaded"
                else layout.attachment_key(item.attachment_id, item.filename),
            )
        )
    session.add(
        Publication(
            id=new_uuid(),
            note_id=plan.draft.kb_id,
            object_key=note_key,
            sha256=_hash_text(plan.note_text),
            etag=etag,
            published_at=now,
        )
    )


def run_import(
    settings: Settings,
    *,
    export_path: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    target_prefix: str | None = None,
    store: S3Store | None = None,
) -> ImportStats:
    """Импортирует экспорт. `dry_run` не пишет ни в БД, ни в S3."""
    if settings.html_chat_id is None:
        msg = "не задан KBC_HTML_CHAT_ID: chat_id в HTML-экспорте отсутствует"
        raise HtmlImportError(msg)

    source_path = export_path if export_path is not None else settings.html_export_dir
    if source_path is None:
        msg = "не задан KBC_HTML_EXPORT_DIR и не передан путь к экспорту"
        raise HtmlImportError(msg)

    layout = discover_export(source_path)
    vault = VaultLayout(target_prefix or settings.s3_vault_prefix)
    chat_id = settings.html_chat_id

    stats = ImportStats(dry_run=dry_run, target_prefix=vault.vault_prefix, pages=len(layout.pages))

    active_store: S3Store | None = store
    session_factory: sessionmaker[Session] | None = None
    source_id: str | None = None

    if not dry_run:
        active_store = active_store or S3Store.from_settings(settings)
        session_factory = build_session_factory(build_engine(settings.db_path))
        source_id, archive_dir = archive_export(layout.pages, settings.raw_archive_dir)
        logger.info("archive_created", source_id=source_id, path=str(archive_dir))
        with session_factory() as session:
            session.add(
                Source(
                    id=source_id,
                    kind=SOURCE_KIND,
                    location=str(layout.root),
                    started_at=datetime.now(UTC),
                    stats_json="{}",
                )
            )
            session.commit()

    processed = 0
    for message in iter_messages(layout):
        if limit is not None and processed >= limit:
            break
        processed += 1
        stats.parsed += 1

        if is_bot_command(message.text):
            # Команды боту (например `/start`) — не материал для базы знаний.
            stats.skipped_commands += 1
            continue

        plan = build_plan(
            message,
            chat_id=chat_id,
            export_root=layout.root,
            layout=vault,
        )

        if dry_run:
            if stats.sample_path is None:
                sample = settings.data_dir / "import-sample.md"
                sample.parent.mkdir(parents=True, exist_ok=True)
                sample.write_text(plan.note_text, encoding="utf-8")
                stats.sample_path = sample
            stats.attachments_not_included += sum(
                1 for item in plan.attachments if item.status == STATUS_NOT_INCLUDED
            )
            stats.attachments_missing += sum(
                1 for item in plan.attachments if item.status == STATUS_MISSING
            )
            continue

        if session_factory is None or active_store is None:
            msg = "внутренняя ошибка: импорт без БД или S3 невозможен"
            raise HtmlImportError(msg)

        with session_factory() as session:
            if _note_exists(session, chat_id=chat_id, message_id=message.message_id):
                stats.notes_skipped += 1
                continue

            try:
                outcome = publish_note(
                    note_key=plan.note_key,
                    note_text=plan.note_text,
                    attachments=list(plan.attachments),
                    layout=vault,
                    store=active_store,
                )
                etag = outcome.note_stat.etag if outcome.note_stat is not None else None
                if outcome.created:
                    stats.notes_created += 1
                    stats.imported_keys.append(outcome.note_key)
                else:
                    stats.notes_already_present += 1
                    if etag is None:
                        existing = active_store.head(outcome.note_key)
                        etag = existing.etag if existing else None

                stats.attachments_uploaded += outcome.uploaded
                stats.attachments_existing += outcome.existing
                stats.attachments_not_included += sum(
                    1 for item in plan.attachments if item.status == STATUS_NOT_INCLUDED
                )
                stats.attachments_missing += sum(
                    1 for item in plan.attachments if item.status == STATUS_MISSING
                )

                raw = _store_raw_message(session, message, chat_id=chat_id)
                _store_note(
                    session,
                    plan,
                    raw=raw,
                    note_key=outcome.note_key,
                    etag=etag,
                    layout=vault,
                )
                session.commit()
            except (S3Error, OSError) as exc:
                # Сбой сети или хранилища не должен обрывать пакетный импорт:
                # сообщение останется необработанным и будет повторено при следующем запуске.
                session.rollback()
                stats.failed_messages += 1
                if len(stats.failures) < 20:
                    stats.failures.append(f"сообщение {message.message_id}: {exc}")
                logger.warning(
                    "import_message_failed",
                    message_id=message.message_id,
                    error=str(exc),
                )
                continue

        if stats.parsed % 100 == 0:
            logger.info("import_progress", parsed=stats.parsed, created=stats.notes_created)

    if not dry_run:
        _finish_source(session_factory, source_id, stats)

    return stats


def _finish_source(
    session_factory: sessionmaker[Session] | None,
    source_id: str | None,
    stats: ImportStats,
) -> None:
    if source_id is None or session_factory is None:
        return
    with session_factory() as session:
        source = session.get(Source, source_id)
        if source is None:
            return
        source.finished_at = datetime.now(UTC)
        source.stats_json = json.dumps(
            {
                "parsed": stats.parsed,
                "skipped_commands": stats.skipped_commands,
                "notes_created": stats.notes_created,
                "notes_skipped": stats.notes_skipped,
                "attachments_uploaded": stats.attachments_uploaded,
                "attachments_missing": stats.attachments_missing,
                "attachments_not_included": stats.attachments_not_included,
            },
            ensure_ascii=False,
        )
        session.commit()


def describe_layout(layout: ExportLayout) -> str:
    pages = ", ".join(layout.page_names)
    return f"root={layout.root} pages=[{pages}]"
