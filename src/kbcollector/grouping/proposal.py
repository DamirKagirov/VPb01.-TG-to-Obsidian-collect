"""Предложение об объединении сообщений в `Review/`.

Файлы предложений — технические: в них больше служебных полей, чем в заметках,
потому что это не пользовательский материал. Опубликованные заметки не меняются.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from kbcollector.domain.ids import kb_id_for
from kbcollector.domain.message import SOURCE_HTML_EXPORT, ParsedMessage
from kbcollector.domain.paths import note_filename
from kbcollector.grouping.grouping import GroupCandidate
from kbcollector.ingestion.html_export.parser import derive_title
from kbcollector.markdown.frontmatter import render_frontmatter
from kbcollector.publication.layout import INBOX_CATEGORY, VaultLayout


def proposal_id(group: GroupCandidate) -> str:
    payload = f"{group.kind}:" + ",".join(str(item) for item in group.message_ids)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:8]
    return f"group-{digest}"


def note_key_for(
    message: ParsedMessage,
    *,
    chat_id: int,
    layout: VaultLayout,
    category: str = INBOX_CATEGORY,
) -> str:
    """Ключ заметки, который создаст импорт (без подготовки вложений)."""
    kb_id = kb_id_for(SOURCE_HTML_EXPORT, chat_id, message.message_id)
    title = derive_title(message.text, message.attachments, message.message_id)
    return layout.note_key(category, note_filename(title, kb_id))


def proposal_key(group: GroupCandidate, *, chat_id: int, layout: VaultLayout) -> str:
    kb_id = kb_id_for(SOURCE_HTML_EXPORT, chat_id, group.primary_id)
    return f"{layout.vault_prefix}Review/{kb_id}/{proposal_id(group)}.md"


def render_proposal(
    group: GroupCandidate,
    messages: dict[int, ParsedMessage],
    *,
    chat_id: int,
    layout: VaultLayout,
    proposed_at: datetime | None = None,
) -> str:
    moment = proposed_at or datetime.now(UTC)
    frontmatter = render_frontmatter(
        {
            "proposal_id": proposal_id(group),
            "kind": group.kind,
            "status": "proposed",
            "created_at": moment.isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
    )

    key = proposal_key(group, chat_id=chat_id, layout=layout)
    lines = [frontmatter, "# Предложение объединения сообщений", ""]
    lines.append(f"Причина: {group.reason}.")
    lines.append("")
    lines.append("## Сообщения")
    lines.append("")
    for message_id in group.message_ids:
        message = messages.get(message_id)
        if message is None:
            lines.append(f"- `{message_id}` — нет данных")
            continue
        note_key = note_key_for(message, chat_id=chat_id, layout=layout)
        link = layout.link_from_note(key, note_key)
        title = derive_title(message.text, message.attachments, message.message_id)
        stamp = message.sent_at.strftime("%Y-%m-%d %H:%M")
        lines.append(f"- `{message_id}` — {stamp} — [{title}](<{link}>)")
    lines.append("")
    lines.append("## Что делать")
    lines.append("")
    lines.append(
        "Объединение **не выполнено автоматически**: опубликованные заметки не меняются "
        "(ТЗ §4.4). Проверьте состав группы и примените объединение вручную либо "
        "дождитесь команды применения предложений."
    )
    lines.append("")
    return "\n".join(lines)


def local_report_path(directory: Path, group: GroupCandidate) -> Path:
    return directory / f"{proposal_id(group)}.md"
