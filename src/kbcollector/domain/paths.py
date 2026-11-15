"""Безопасные имена файлов и путей (ТЗ §11.3).

Учитываются ограничения Windows: запрещённые символы, зарезервированные имена,
точки и пробелы в конце, ограничение длины. Кириллица и emoji сохраняются
(`allow_unicode=True`) — стабильность идентичности важнее «красивого» ASCII-имени.
"""

from __future__ import annotations

import re
import unicodedata

from pathvalidate import sanitize_filename
from slugify import slugify

from kbcollector.domain.ids import short_suffix

MAX_FILENAME_LENGTH = 120
MAX_SLUG_LENGTH = 80

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_TRAILING_JUNK = re.compile(r"[\s._-]+$")


def _safe_unicode(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def slugify_title(title: str, *, max_length: int = MAX_SLUG_LENGTH) -> str:
    """Человекочитаемая часть имени: без запрещённых символов, с кириллицей."""
    cleaned = _safe_unicode(title).replace("/", " ").replace("\\", " ")
    slug = slugify(
        cleaned,
        max_length=max_length,
        separator="-",
        allow_unicode=True,
        lowercase=False,
    )
    slug = _TRAILING_JUNK.sub("", slug)
    slug = sanitize_filename(slug, platform="windows", replacement_text="-")
    if not slug or slug.lower() in _WINDOWS_RESERVED:
        slug = f"note-{slug}" if slug else "note"
    return slug


def note_filename(title: str, kb_id: str) -> str:
    """Имя заметки: `slug-суффикс.md`. Суффикс обеспечивает уникальность."""
    suffix = short_suffix(kb_id)
    budget = MAX_FILENAME_LENGTH - len(suffix) - len(".md") - 1
    slug = slugify_title(title, max_length=min(budget, MAX_SLUG_LENGTH))
    return f"{slug}-{suffix}.md"


def attachment_filename(original_name: str) -> str:
    """Безопасное имя файла вложения; расширение сохраняется."""
    name = sanitize_filename(
        _safe_unicode(original_name).replace("/", "_").replace("\\", "_"),
        platform="windows",
        replacement_text="_",
    )
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    stem = _TRAILING_JUNK.sub("", stem) or "file"
    if stem.lower() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    budget = MAX_FILENAME_LENGTH - (len(extension) + 1 if extension else 0)
    stem = stem[:budget]
    return f"{stem}.{extension}" if extension else stem
