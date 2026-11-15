"""Идентификаторы: заметки, источники, вложения.

Правила (ТЗ §4.3, §10.2):
* `kb_id` создаётся один раз и не выводится из пути, заголовка или текста;
* `attachment_id` детерминирован: один и тот же файл в одном сообщении
  получает один и тот же идентификатор при повторном импорте.
"""

from __future__ import annotations

import hashlib
from uuid import NAMESPACE_URL, uuid5

from uuid_utils import uuid7

__all__ = [
    "attachment_id",
    "kb_id_for",
    "new_kb_id",
    "new_source_id",
    "new_uuid",
    "short_suffix",
]


def new_uuid() -> str:
    """UUIDv7 строкой: монотонный, сортируемый, генерируется локально."""
    return str(uuid7())


def new_kb_id() -> str:
    return f"note_{uuid7()}"


def new_source_id() -> str:
    return new_uuid()


def kb_id_for(source_kind: str, chat_id: int, message_id: int) -> str:
    """Детерминированный `kb_id` для заметки, созданной из одного сообщения.

    Детерминированность важна для идемпотентности: повторный импорт того же
    сообщения (даже при потере БД) даёт тот же `kb_id` и тот же ключ объекта,
    поэтому вторая копия не создаётся.
    """
    key = f"{source_kind}:{chat_id}:{message_id}"
    return f"note_{uuid5(NAMESPACE_URL, f'kbcollector/{key}')}"


def short_suffix(kb_id: str, length: int = 6) -> str:
    """Короткий суффикс из kb_id для имени файла."""
    tail = kb_id.rsplit("_", 1)[-1].replace("-", "")
    return tail[-length:]


def attachment_id(chat_id: int, message_id: int, rel_path: str) -> str:
    """Детерминированный id вложения (не зависит от времени импорта)."""
    digest = hashlib.sha256(f"{chat_id}:{message_id}:{rel_path}".encode()).hexdigest()
    return f"att_{digest[:24]}"
