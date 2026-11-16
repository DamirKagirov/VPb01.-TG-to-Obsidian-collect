"""Предложения LLM-обогащения в `Review/` (опубликованные заметки не меняются)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from kbcollector.domain.ids import kb_id_for
from kbcollector.domain.message import SOURCE_HTML_EXPORT
from kbcollector.llm.schema import Enrichment
from kbcollector.markdown.frontmatter import render_frontmatter
from kbcollector.publication.layout import VaultLayout


def enrichment_proposal_id(input_hash: str) -> str:
    return f"enrich-{input_hash[:8]}"


def enrichment_proposal_key(
    *, chat_id: int, message_id: int, input_hash: str, layout: VaultLayout
) -> str:
    kb_id = kb_id_for(SOURCE_HTML_EXPORT, chat_id, message_id)
    return f"{layout.vault_prefix}Review/{kb_id}/{enrichment_proposal_id(input_hash)}.md"


def render_enrichment_proposal(
    enrichment: Enrichment,
    *,
    input_hash: str,
    note_key: str,
    base_sha256: str | None,
    current_title: str,
    proposed_at: datetime | None = None,
) -> str:
    moment = proposed_at or datetime.now(UTC)
    frontmatter = render_frontmatter(
        {
            "proposal_id": enrichment_proposal_id(input_hash),
            "kind": "enrichment",
            "status": "proposed",
            "created_at": moment.isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
    )

    tags = ", ".join(enrichment.tags) if enrichment.tags else "—"
    lines = [
        frontmatter,
        "# Предложение обогащения",
        "",
        f"- Заметка: `{note_key}`",
        f"- Текущий заголовок: {current_title}",
        f"- Предлагаемый заголовок: {enrichment.title}",
        f"- Категория: **{enrichment.category}** (уверенность {enrichment.confidence:.2f})",
        f"- Теги: {tags}",
    ]
    if base_sha256:
        lines.append(f"- Базовая версия (sha256): `{base_sha256[:12]}…`")
    lines.extend(
        [
            "",
            "## Что делать",
            "",
            "Предложение **не применено автоматически**: опубликованная заметка не изменена "
            "(ТЗ §4.4). Применение — отдельной командой, с проверкой актуальной версии "
            "и сохранением предыдущей.",
            "",
        ]
    )
    return "\n".join(lines)


def hash_proposal_note(note_text: str) -> str:
    return hashlib.sha256(note_text.encode("utf-8")).hexdigest()
