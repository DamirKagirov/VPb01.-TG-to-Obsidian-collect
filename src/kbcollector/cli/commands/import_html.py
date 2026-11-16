"""Команда `kbcollector import-html`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from kbcollector.cli.runtime import console, load_settings
from kbcollector.ingestion.html_export.discovery import ExportLayoutError
from kbcollector.ingestion.html_export.parser import ExportParseError
from kbcollector.publication.s3_client import S3Error
from kbcollector.services.import_html import HtmlImportError, ImportStats, run_import


def import_html(
    path: Path | None = typer.Option(
        None,
        "--path",
        help="Каталог или файл экспорта (по умолчанию KBC_HTML_EXPORT_DIR).",
        exists=False,
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Обработать не более N сообщений (для проверки среза).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Только разбор и рендеринг: без записи в БД и S3.",
    ),
    target_prefix: str | None = typer.Option(
        None,
        "--target-prefix",
        help="Префикс vault для публикации (например, _stage3 для проверки).",
    ),
) -> None:
    """Импортировать HTML-экспорт Telegram в vault и БД."""
    settings = load_settings()

    try:
        stats = run_import(
            settings,
            export_path=path,
            limit=limit,
            dry_run=dry_run,
            target_prefix=target_prefix,
        )
    except HtmlImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except (ExportLayoutError, ExportParseError) as exc:
        console.print(f"[red]Ошибка разбора экспорта:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except S3Error as exc:
        console.print(f"[red]Ошибка публикации:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _render_stats(stats)


def _render_stats(stats: ImportStats) -> None:
    title = "import-html (dry-run)" if stats.dry_run else "import-html"
    table = Table(title=title, header_style="bold")
    table.add_column("Показатель", no_wrap=True)
    table.add_column("Значение", overflow="fold")

    rows = [
        ("Префикс назначения", stats.target_prefix),
        ("Страниц экспорта", str(stats.pages)),
        ("Разобрано сообщений", str(stats.parsed)),
        ("Пропущено команд боту", str(stats.skipped_commands)),
        ("Создано заметок", str(stats.notes_created)),
        ("Уже было в БД (пропущено)", str(stats.notes_skipped)),
        ("Объект уже существовал", str(stats.notes_already_present)),
        ("Вложений загружено", str(stats.attachments_uploaded)),
        ("Вложений уже было", str(stats.attachments_existing)),
        ("Вложений отсутствует", str(stats.attachments_missing)),
        ("Вложений не включено в экспорт", str(stats.attachments_not_included)),
        ("Сбоев (будут повторены)", str(stats.failed_messages)),
    ]
    if stats.sample_path is not None:
        rows.append(("Пример заметки", str(stats.sample_path)))
    if stats.imported_keys:
        preview = "\n".join(stats.imported_keys[:5])
        if len(stats.imported_keys) > 5:
            preview += f"\n… ещё {len(stats.imported_keys) - 5}"
        rows.append(("Новые ключи", preview))
    if stats.failures:
        rows.append(("Ошибки", "\n".join(stats.failures[:5])))

    for name, value in rows:
        table.add_row(name, value)

    console.print(table)

    if stats.dry_run:
        console.print("[yellow]dry-run: в БД и S3 ничего не записано[/yellow]")
    if stats.failed_messages:
        console.print(
            f"[yellow]Сбоев: {stats.failed_messages}. "
            "Повторите запуск — обработанные сообщения будут пропущены.[/yellow]"
        )
        raise typer.Exit(code=1)
