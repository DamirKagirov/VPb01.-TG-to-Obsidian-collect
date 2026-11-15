"""Схема базы состояния (SQLite)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс декларативных моделей."""


class Source(Base):
    """Запуск импорта или иной источник данных."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    location: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats_json: Mapped[str] = mapped_column(Text, default="{}")


class RawMessage(Base):
    """Исходное сообщение: одна запись на (источник, чат, message_id)."""

    __tablename__ = "raw_messages"
    __table_args__ = (
        UniqueConstraint("source_kind", "chat_id", "message_id", name="uq_raw_message_identity"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(32))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    forward_author: Mapped[str | None] = mapped_column(Text, nullable=True)
    forward_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_to_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_joined: Mapped[bool] = mapped_column(default=False)
    text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MessageVersion(Base):
    """Версия текста сообщения: правка в чате создаёт новую версию."""

    __tablename__ = "message_versions"
    __table_args__ = (
        UniqueConstraint("raw_message_id", "content_hash", name="uq_message_version"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    raw_message_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("raw_messages.id", ondelete="CASCADE")
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Attachment(Base):
    """Вложение и его статус (ТЗ §13.1)."""

    __tablename__ = "attachments"
    __table_args__ = (
        UniqueConstraint("raw_message_id", "position", name="uq_attachment_position"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    raw_message_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("raw_messages.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16))
    original_name: Mapped[str] = mapped_column(Text)
    rel_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="missing")
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class Note(Base):
    """Опубликованная заметка: `kb_id` идентифицирует её независимо от пути."""

    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("chat_id", "message_id", name="uq_note_source_message"),)

    kb_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(120), default="Inbox")
    current_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="draft")
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Publication(Base):
    """Факт публикации объекта."""

    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("object_key", name="uq_publication_key"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(48), ForeignKey("notes.kb_id"))
    object_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Job(Base):
    """Устойчивая очередь заданий (используется с этапа 4)."""

    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_job_dedupe"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    state: Mapped[str] = mapped_column(String(16), default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Meta(Base):
    """Служебные значения: last_update_id, версия схемы и т. п."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
