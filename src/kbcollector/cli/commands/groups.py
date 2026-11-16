"""Команда `kbcollector groups` — кандидаты на объединение сообщений.

Ничего не меняет в опубликованных заметках. С `--publish` пишет предложения
в `Review/<kb_id>/<proposal_id>.md` (ТЗ §4.4, §9.2).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import typer
from rich.table import Table

from kbcollector.cli.runtime import console, load_settings
from kbcollector.config import Settings
from kbcollector.domain.message import ParsedMessage
from kbcollector.grouping.grouping import GroupCandidate, find_groups, unsupported_signals
from kbcollector.grouping.proposal import proposal_key, render_proposal
from kbcollector.ingestion.html_export.discovery import ExportLayoutError, discover_export
from kbcollector.ingestion.html_export.parser import ExportParseError, is_bot_command, iter_messages
from kbcollector.publication.layout import VaultLayout
from kbcollector.publication.s3_client import S3Error, S3Store


def groups(
    path: Path | None = typer.Option(
        None,
        "--path",
        help="Каталог экспорта (по умолчанию KBC_HTML_EXPORT_DIR).",
    ),
    publish: bool = typer.Option(
        False,
        "--publish",
        help="Записать предложения в Review/ (по умолчанию только показать).",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Не более N групп.",
    ),
    sample: int = typer.Option(5, "--sample", min=0, help="Сколько примеров показать."),
) -> None:
    """Найти связанные сообщения и, при желании, предложить объединение."""
    settings = load_settings()
    if settings.html_chat_id is None:
        console.print("[red]Не задан KBC_HTML_CHAT_ID[/red]")
        raise typer.Exit(code=2)

    source_path = path or settings.html_export_dir
    if source_path is None:
        console.print("[red]Не задан KBC_HTML_EXPORT_DIR и не передан --path[/red]")
        raise typer.Exit(code=2)

    try:
        layout = discover_export(source_path)
        messages = [m for m in iter_messages(layout) if not is_bot_command(m.text)]
    except (ExportLayoutError, ExportParseError) as exc:
        console.print(f"[red]Ошибка разбора экспорта:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    candidates = find_groups(messages)
    if limit is not None:
        candidates = candidates[:limit]

    by_id = {message.message_id: message for message in messages}
    vault = VaultLayout(settings.s3_vault_prefix)

    _render_summary(candidates, sample=sample)

    if publish:
        _publish(candidates, by_id, settings=settings, vault=vault)


def _render_summary(
    candidates: list[GroupCandidate],
    *,
    sample: int,
) -> None:
    counts = Counter(group.kind for group in candidates)
    table = Table(title="groups: кандидаты на объединение", header_style="bold")
    table.add_column("Сигнал", no_wrap=True)
    table.add_column("Групп", justify="right")
    table.add_column("Примеры", overflow="fold")

    examples: dict[str, list[str]] = {}
    for group in candidates:
        preview = examples.setdefault(group.kind, [])
        if len(preview) < sample:
            preview.append(", ".join(str(item) for item in group.message_ids))

    for kind in sorted(counts) or ["—"]:
        table.add_row(
            kind,
            str(counts.get(kind, 0)),
            "\n".join(examples.get(kind, [])) or "—",
        )
    console.print(table)

    if not candidates:
        console.print("[yellow]Кандидатов нет.[/yellow]")

    for kind, reason in unsupported_signals().items():
        console.print(f"[dim]{kind}: {reason}[/dim]")


def _publish(
    candidates: list[GroupCandidate],
    by_id: dict[int, ParsedMessage],
    *,
    settings: Settings,
    vault: VaultLayout,
) -> None:
    try:
        store = S3Store.from_settings(settings)
    except S3Error as exc:
        console.print(f"[red]S3 не настроен:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    created = 0
    existing = 0
    try:
        for group in candidates:
            chat_id = settings.html_chat_id
            if chat_id is None:
                raise typer.Exit(code=2)
            key = proposal_key(group, chat_id=chat_id, layout=vault)
            body = render_proposal(
                group,
                by_id,
                chat_id=chat_id,
                layout=vault,
            )
            stat = store.put_if_absent(key, body.encode("utf-8"), "text/markdown; charset=utf-8")
            if stat is None:
                existing += 1
            else:
                created += 1
    except S3Error as exc:
        console.print(f"[red]Ошибка публикации:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Review/: создано предложений {created}, уже было {existing} "
        f"(всего кандидатов {len(candidates)})"
    )
