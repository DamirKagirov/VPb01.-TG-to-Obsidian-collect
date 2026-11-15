"""Разбор HTML-экспорта Telegram Desktop в доменные сообщения.

Реализованы правила из `docs/import.md`:
* ID сообщения — `message<telegram_message_id>`; служебные блоки пропускаются;
* дата берётся только из атрибута `title` (часовой пояс из строки);
* автор пересылки и автор оригинала — разные узлы;
* порядок сообщений — документный, продолжения `joined` не сливаются;
* вложения разрешаются относительно каталога экспорта, выход за границы запрещён.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from kbcollector.domain.message import AttachmentKind, AttachmentRef, ForwardInfo, ParsedMessage
from kbcollector.ingestion.html_export.discovery import ExportLayout
from kbcollector.ingestion.html_export.text import render_message_text
from kbcollector.markdown.sanitize import sanitize_text

_DATE_FORMATS = ("%d.%m.%Y %H:%M:%S GMT%z", "%d.%m.%Y %H:%M GMT%z")
_ATTACHMENT_PREFIXES = ("photos/", "files/", "video_files/", "stickers/")
_TITLE_LIMIT = 80
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKUP_CHARS = re.compile(r"[`*_>#|]+")
_MESSAGE_ID_RE = re.compile(r"^message(\d+)$")
_COMMAND_RE = re.compile(r"^/[A-Za-z0-9_]{1,64}(?:@[A-Za-z0-9_]{1,64})?$")


def is_bot_command(text: str) -> bool:
    """True, если сообщение — только команда боту (`/start`, `/help@bot`).

    Учитывает, что команда может быть уже отрендерена как inline-код.
    """
    candidate = text.strip()
    if len(candidate) >= 2 and candidate.startswith("`") and candidate.endswith("`"):
        candidate = candidate[1:-1].strip()
    return bool(_COMMAND_RE.match(candidate))


class ExportParseError(ValueError):
    """Структура экспорта не соответствует ожидаемой."""


def _classes(node: Tag) -> set[str]:
    raw = node.get("class")
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, str):
        return set(raw.split())
    return set()


def _parse_date(raw: str) -> datetime:
    value = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            # Форматы содержат %z, поэтому результат всегда timezone-aware.
            return datetime.strptime(value, fmt)  # noqa: DTZ007
        except ValueError:
            continue
    msg = f"не удалось разобрать дату: {raw!r}"
    raise ExportParseError(msg)


def _direct_child(parent: Tag, class_name: str) -> Tag | None:
    found = parent.find("div", class_=class_name, recursive=False)
    return found if isinstance(found, Tag) else None


def _direct_children(parent: Tag) -> list[Tag]:
    return list(parent.find_all("div", recursive=False))


def _find_direct_by_classes(parent: Tag, required: frozenset[str]) -> Tag | None:
    for child in _direct_children(parent):
        if required <= _classes(child):
            return child
    return None


def _author_text(node: Tag | None) -> str | None:
    if node is None:
        return None
    texts = [
        str(child).strip()
        for child in node.children
        if isinstance(child, NavigableString) and str(child).strip()
    ]
    if texts:
        return " ".join(texts)
    fallback = node.get_text(" ", strip=True)
    return fallback or None


def _find_forward_body(body: Tag) -> Tag | None:
    for child in _direct_children(body):
        classes = _classes(child)
        if "forwarded" in classes and "body" in classes:
            return child
    return None


def _reply_target(body: Tag) -> int | None:
    container = body.find("div", class_="reply_to")
    if not isinstance(container, Tag):
        return None
    anchor = container.find("a", href=re.compile(r"^#go_to_message\d+$"))
    if not isinstance(anchor, Tag):
        return None
    match = re.search(r"(\d+)$", str(anchor.get("href", "")))
    return int(match.group(1)) if match else None


def _attachment_kind(anchor: Tag, href: str) -> AttachmentKind:
    classes = _classes(anchor)
    if href.startswith("stickers/"):
        return "sticker"
    if href.startswith("video_files/") or "video_file_wrap" in classes:
        return "video"
    if href.startswith("photos/") or "photo_wrap" in classes:
        return "photo"
    if "media_file" in classes or href.startswith("files/"):
        return "document"
    return "other"


def _attachment_refs(content: Tag) -> tuple[AttachmentRef, ...]:
    refs: list[AttachmentRef] = []
    seen: set[tuple[str, str, str | None]] = set()

    for anchor in content.find_all("a", href=True):
        href = unquote(str(anchor.get("href", "")).strip())
        if not href.startswith(_ATTACHMENT_PREFIXES):
            continue
        kind = _attachment_kind(anchor, href)
        original_name = _attachment_name(anchor, href)
        size_hint = _attachment_size(anchor)
        run_signature = (kind, original_name, href)
        if run_signature in seen:
            continue
        seen.add(run_signature)
        refs.append(
            AttachmentRef(
                kind=kind,
                original_name=original_name,
                rel_path=href,
                size_hint=size_hint,
                included=True,
            )
        )

    # Медиа, не включённые в экспорт: Telegram оставляет заглушку без href.
    for wrapper in content.find_all("div", class_="media_wrap"):
        if wrapper.find("a", href=True) is not None:
            continue
        for placeholder in wrapper.find_all("div", class_="media", recursive=False):
            kind = _placeholder_kind(placeholder)
            original_name = _attachment_name(placeholder, "")
            size_hint = _attachment_size(placeholder)
            placeholder_signature = (kind, original_name, None)
            if placeholder_signature in seen:
                continue
            seen.add(placeholder_signature)
            refs.append(
                AttachmentRef(
                    kind=kind,
                    original_name=original_name,
                    rel_path=None,
                    size_hint=size_hint,
                    included=False,
                )
            )

    return tuple(refs)


def _attachment_name(anchor: Tag, href: str) -> str:
    title_node = anchor.find("div", class_="title")
    if isinstance(title_node, Tag) and title_node.get_text(strip=True):
        return title_node.get_text(" ", strip=True)
    if href:
        return Path(href).name
    return "вложение"


def _attachment_size(anchor: Tag) -> str | None:
    status_node = anchor.find("div", class_="status")
    if isinstance(status_node, Tag):
        text = status_node.get_text(" ", strip=True)
        return text or None
    return None


def _placeholder_kind(node: Tag) -> AttachmentKind:
    classes = _classes(node)
    if "media_photo" in classes:
        return "photo"
    if "media_video" in classes:
        return "video"
    if "media_audio" in classes:
        return "audio"
    if "media_sticker" in classes:
        return "sticker"
    if "media_file" in classes:
        return "document"
    return "other"


def _forward_info(forward_body: Tag | None) -> ForwardInfo | None:
    if forward_body is None:
        return None
    name_node = _direct_child(forward_body, "from_name")
    date_node = name_node.find("span", class_="date") if isinstance(name_node, Tag) else None
    sent_at: datetime | None = None
    if isinstance(date_node, Tag):
        title = date_node.get("title")
        if isinstance(title, str) and title.strip():
            sent_at = _parse_date(title)
    return ForwardInfo(author=_author_text(name_node), sent_at=sent_at)


def _text_node(content: Tag) -> Tag | None:
    node = content.find("div", class_="text")
    return node if isinstance(node, Tag) else None


def derive_title(text: str, attachments: tuple[AttachmentRef, ...], message_id: int) -> str:
    """Заголовок заметки: первая содержательная строка текста."""
    plain = _LINK_RE.sub(r"\1", text)
    plain = _MARKUP_CHARS.sub("", plain)
    for line in plain.split("\n"):
        candidate = " ".join(line.split())
        if candidate:
            if len(candidate) > _TITLE_LIMIT:
                candidate = candidate[: _TITLE_LIMIT - 1].rstrip() + "…"
            return candidate
    if attachments:
        return attachments[0].original_name
    return f"Сообщение {message_id}"


def parse_message(node: Tag, *, source_page: str | None = None) -> ParsedMessage | None:
    """Разбирает один `div.message`; служебные блоки возвращают `None`."""
    classes = _classes(node)
    if "service" in classes:
        return None

    match = _MESSAGE_ID_RE.match(str(node.get("id", "")))
    if not match:
        return None
    message_id = int(match.group(1))

    body = node.find("div", class_="body", recursive=False)
    if not isinstance(body, Tag):
        msg = f"сообщение {message_id}: отсутствует div.body"
        raise ExportParseError(msg)

    date_node = _find_direct_by_classes(body, frozenset({"date", "details"}))
    if date_node is None:
        msg = f"сообщение {message_id}: отсутствует блок даты"
        raise ExportParseError(msg)
    title_attr = date_node.get("title")
    if not isinstance(title_attr, str) or not title_attr.strip():
        msg = f"сообщение {message_id}: пустой атрибут title у даты"
        raise ExportParseError(msg)
    sent_at = _parse_date(title_attr)

    forward_body = _find_forward_body(body)
    content = forward_body if forward_body is not None else body

    text_node = _text_node(content)
    raw_text = render_message_text(text_node) if text_node is not None else ""
    text = sanitize_text(raw_text)

    attachments = _attachment_refs(content)
    reply_to = _reply_target(body)

    raw_payload: dict[str, object] = {
        "classes": sorted(classes),
        "reply_to": reply_to,
        "attachments": [
            {
                "rel_path": ref.rel_path,
                "kind": ref.kind,
                "original_name": ref.original_name,
                "included": ref.included,
            }
            for ref in attachments
        ],
    }
    forward = _forward_info(forward_body)
    if forward is not None:
        raw_payload["forward"] = {
            "author": forward.author,
            "sent_at": forward.sent_at.isoformat() if forward.sent_at else None,
        }

    return ParsedMessage(
        message_id=message_id,
        sent_at=sent_at,
        author=_author_text(_direct_child(body, "from_name")),
        text=text,
        joined="joined" in classes,
        forward=forward,
        reply_to_id=reply_to,
        attachments=attachments,
        source_page=source_page,
        raw_payload=raw_payload,
    )


def iter_messages(layout: ExportLayout) -> Iterator[ParsedMessage]:
    """Последовательно разбирает все страницы экспорта в документном порядке."""
    for page in layout.pages:
        try:
            html = page.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"не удалось прочитать {page}: {exc}"
            raise ExportParseError(msg) from exc
        except UnicodeDecodeError as exc:
            msg = f"{page} не является корректным UTF-8: {exc}"
            raise ExportParseError(msg) from exc

        soup = BeautifulSoup(html, "lxml")
        for node in soup.select("div.message"):
            parsed = parse_message(node, source_page=page.name)
            if parsed is not None:
                yield parsed
