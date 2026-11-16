"""Рендеринг заметки Obsidian из доменного черновика.

Структура соответствует ТЗ §11.2, но пустые разделы не создаются.
Текст пользователя и текст сообщения не смешиваются: материалы идут отдельными
секциями, «Краткое содержание» появится только на этапе LLM-обогащения.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from kbcollector.domain.message import NoteDraft
from kbcollector.markdown.frontmatter import render_frontmatter
from kbcollector.markdown.sanitize import sanitize_text

SCHEMA_VERSION = 1
_TOKEN_RE = re.compile(r"\{\{att:([^}]+)\}\}")


@dataclass(frozen=True, slots=True)
class AttachmentView:
    """Вложение в том виде, в каком оно попадает в заметку."""

    rel_path: str | None
    original_name: str
    link: str | None
    size_hint: str | None = None
    kind: str = "other"
    problem: str | None = None


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _link_target(target: str) -> str:
    """Markdown-ссылки с пробелами или скобками требуют обрамления `<>`."""
    if any(char in target for char in " ()<>"):
        return f"<{target}>"
    return target


def _substitute_tokens(text: str, views: dict[str, AttachmentView]) -> str:
    def replace(match: re.Match[str]) -> str:
        view = views.get(match.group(1))
        if view is None:
            return ""
        if view.link is None:
            return f"«{view.original_name}» (файл отсутствует)"
        return f"[{view.original_name}]({_link_target(view.link)})"

    return _TOKEN_RE.sub(replace, text)


def _attachments_section(views: list[AttachmentView]) -> list[str]:
    if not views:
        return []
    lines = ["## Вложения", ""]
    for view in views:
        suffix = f" — {view.size_hint}" if view.size_hint else ""
        if view.link is None:
            reason = view.problem or "файл отсутствует в экспорте"
            lines.append(f"- `{view.original_name}`{suffix} — {reason}")
        else:
            lines.append(f"- [{view.original_name}]({_link_target(view.link)}){suffix}")
    lines.append("")
    return lines


def _sources_section(draft: NoteDraft) -> list[str]:
    lines = ["## Источники", ""]
    lines.append(
        f"- Чат `{draft.chat_id}`, сообщение `{draft.message_id}` — {_iso(draft.created_at)}"
    )
    if draft.forward is not None:
        author = draft.forward.author or "автор не указан"
        if draft.forward.sent_at is not None:
            lines.append(f"- Переслано из: {author}, {_iso(draft.forward.sent_at)}")
        else:
            lines.append(f"- Переслано из: {author}")
    if draft.reply_to_id is not None:
        lines.append(f"- Ответ на сообщение `{draft.reply_to_id}`")
    lines.append("")
    return lines


def render_note(draft: NoteDraft, attachments: list[AttachmentView]) -> str:
    """Возвращает полный Markdown-файл, готовый к публикации."""
    views = {view.rel_path: view for view in attachments if view.rel_path is not None}
    body_text = sanitize_text(_substitute_tokens(draft.text, views))

    frontmatter = render_frontmatter(
        {
            "kb_id": draft.kb_id,
            "title": draft.title,
            "source": draft.source_kind,
            "chat_id": draft.chat_id,
            "message_id": draft.message_id,
            "created_at": _iso(draft.created_at),
            "category": draft.category,
            "tags": list(draft.tags),
            "schema_version": SCHEMA_VERSION,
        }
    )

    lines = [frontmatter, f"# {draft.title}", ""]

    lines.extend(["## Материалы", "", f"### Сообщение {draft.message_id}", ""])
    if body_text:
        lines.extend([body_text, ""])
    elif attachments:
        lines.extend(["_Медиа без подписи._", ""])
    else:
        lines.extend(["_Пустое сообщение._", ""])

    lines.extend(_attachments_section(attachments))
    lines.extend(_sources_section(draft))

    return "\n".join(lines).rstrip() + "\n"
