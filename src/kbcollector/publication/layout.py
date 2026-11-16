"""Раскладка объектов в bucket (ADR 0005).

`vault/Telegram/<Категория>/<файл>.md`
`vault/Attachments/Telegram/<attachment_id>/<файл>`

Ссылки в заметке — относительные, вычисляются от пути заметки, чтобы перемещение
папки внутри vault не ломало их (ТЗ §11.3).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

TELEGRAM_DIR = "Telegram"
ATTACHMENTS_DIR = "Attachments"
INBOX_CATEGORY = "Inbox"


@dataclass(frozen=True, slots=True)
class VaultLayout:
    """Префиксы и правила построения ключей."""

    vault_prefix: str

    def __post_init__(self) -> None:
        cleaned = self.vault_prefix.strip().strip("/")
        if not cleaned:
            msg = "префикс vault не может быть пустым"
            raise ValueError(msg)
        object.__setattr__(self, "vault_prefix", f"{cleaned}/")

    def note_key(self, category: str, filename: str) -> str:
        category_path = category.strip("/").replace("\\", "/")
        return f"{self.vault_prefix}{TELEGRAM_DIR}/{category_path}/{filename}"

    def attachment_key(self, attachment_id: str, filename: str) -> str:
        return f"{self.vault_prefix}{ATTACHMENTS_DIR}/{TELEGRAM_DIR}/{attachment_id}/{filename}"

    @staticmethod
    def link_from_note(note_key: str, target_key: str) -> str:
        note_dir = posixpath.dirname(note_key)
        return posixpath.relpath(target_key, note_dir)
