"""Обнаружение страниц экспорта и границ каталога.

Telegram Desktop может разбить экспорт на несколько страниц (`messages.html`,
`messages2.html`, …). Парсер обязан сохранять порядок и не выходить за каталог
экспорта (ТЗ §8.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_MESSAGES_RE = re.compile(r"^messages(\d*)\.html$", re.IGNORECASE)
_MAX_SEARCH_DEPTH = 2


class ExportLayoutError(ValueError):
    """Каталог экспорта не найден или неоднозначен."""


@dataclass(frozen=True, slots=True)
class ExportLayout:
    """Корень экспорта и упорядоченный список HTML-страниц."""

    root: Path
    pages: tuple[Path, ...]

    @property
    def page_names(self) -> tuple[str, ...]:
        return tuple(page.name for page in self.pages)


def _page_order(path: Path) -> tuple[int, str]:
    match = _MESSAGES_RE.match(path.name)
    number = int(match.group(1)) if match and match.group(1) else 1
    return number, path.name.lower()


def _find_pages(directory: Path) -> list[Path]:
    return sorted(
        (item for item in directory.iterdir() if item.is_file() and _MESSAGES_RE.match(item.name)),
        key=_page_order,
    )


def _find_export_dir(start: Path, *, depth: int = 0) -> Path:
    pages = _find_pages(start)
    if pages:
        return start
    if depth >= _MAX_SEARCH_DEPTH:
        msg = f"в каталоге {start} нет messages.html"
        raise ExportLayoutError(msg)

    candidates = [
        child
        for child in sorted(start.iterdir())
        if child.is_dir() and not child.name.startswith(".")
    ]
    for candidate in candidates:
        try:
            return _find_export_dir(candidate, depth=depth + 1)
        except ExportLayoutError:
            continue

    msg = f"экспорт Telegram не найден в {start} (глубина поиска {_MAX_SEARCH_DEPTH})"
    raise ExportLayoutError(msg)


def discover_export(path: Path) -> ExportLayout:
    """Принимает файл `messages.html` или каталог, содержащий экспорт."""
    resolved = path.expanduser()
    if not resolved.exists():
        msg = f"путь не существует: {resolved}"
        raise ExportLayoutError(msg)

    if resolved.is_file():
        if not _MESSAGES_RE.match(resolved.name):
            msg = f"ожидался messages*.html, получено: {resolved.name}"
            raise ExportLayoutError(msg)
        root = resolved.parent
        pages = _find_pages(root)
        if resolved not in pages:
            pages.insert(0, resolved)
    else:
        root = _find_export_dir(resolved)
        pages = _find_pages(root)

    if not pages:
        msg = f"в каталоге {root} не найдено ни одной страницы messages*.html"
        raise ExportLayoutError(msg)

    return ExportLayout(root=root, pages=tuple(pages))


def resolve_attachment(root: Path, href: str) -> Path | None:
    """Безопасно разрешает путь вложения внутри каталога экспорта.

    Возвращает `None`, если файл отсутствует или путь выходит за границы экспорта.
    """
    if not href or href.startswith(("http://", "https://", "#", "/", "\\")):
        return None

    root_resolved = root.resolve()
    try:
        candidate = (root_resolved / href).resolve()
    except (OSError, ValueError):
        return None

    if not candidate.is_relative_to(root_resolved):
        return None
    if not candidate.is_file():
        return None
    return candidate
