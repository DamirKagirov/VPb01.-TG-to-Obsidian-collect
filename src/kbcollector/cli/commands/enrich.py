"""Команда `kbcollector enrich` — обогащение существующих заметок."""

from __future__ import annotations

from typing import cast

import typer
from rich.table import Table

from kbcollector.cli.runtime import console, load_settings
from kbcollector.llm.service import (
    DEFAULT_LIMIT,
    EnrichmentSetupError,
    EnrichmentStats,
    run_enrich,
)


def enrich(
    limit: int = typer.Option(
        DEFAULT_LIMIT,
        "--limit",
        min=1,
        help="Сколько заметок обработать за запуск.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Не публиковать предложения (кэш и учёт токенов всё равно записываются).",
    ),
    propose: bool = typer.Option(
        False,
        "--propose",
        help="Записать предложения обогащения в Review/.",
    ),
    budget_tokens: int | None = typer.Option(
        None,
        "--budget-tokens",
        min=1,
        help="Предел расхода за запуск (по умолчанию KBC_LLM_BUDGET_TOKENS).",
    ),
) -> None:
    """Обогатить существующие заметки: заголовок, категория, теги."""
    settings = load_settings()
    try:
        stats = run_enrich(
            settings,
            limit=limit,
            dry_run=dry_run,
            propose=propose,
            budget_tokens=budget_tokens,
        )
    except EnrichmentSetupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    _render_stats(stats)

    if stats.errors:
        console.print(f"[yellow]Ошибок: {stats.errors}[/yellow]")
        for failure in stats.failures[:5]:
            console.print(f"  • {failure}")
        raise typer.Exit(code=1)


def _render_stats(stats: EnrichmentStats) -> None:
    mode = "dry-run" if stats.dry_run else ("propose" if stats.propose else "только расчёт")
    table = Table(title=f"enrich ({mode})", header_style="bold")
    table.add_column("Показатель", no_wrap=True)
    table.add_column("Значение", overflow="fold")

    remaining = max(stats.budget - stats.spent_before - stats.spent_now, 0)
    rows = [
        ("Лимит заметок", str(stats.limit)),
        ("Обработано", str(stats.processed)),
        ("Пропущено без текста", str(stats.skipped_empty)),
        ("Из кэша", str(stats.cached)),
        ("Запросов к модели", str(stats.requested)),
        ("Входных токенов", str(stats.input_tokens)),
        ("Выходных токенов", str(stats.output_tokens)),
        ("Израсходовано до запуска", str(stats.spent_before)),
        ("Израсходовано в запуске", str(stats.spent_now)),
        ("Остаток бюджета", str(remaining)),
        ("Ошибок", str(stats.errors)),
        ("Создано предложений", str(stats.proposals_created)),
        ("Предложений уже было", str(stats.proposals_existing)),
    ]
    for name, value in rows:
        table.add_row(name, value)
    console.print(table)

    if stats.budget_exhausted:
        console.print("[yellow]Бюджет токенов исчерпан — обработка остановлена.[/yellow]")

    if stats.samples:
        console.print("\n[bold]Примеры:[/bold]")
        for sample in stats.samples:
            tags = ", ".join(str(item) for item in cast("list[object]", sample.get("tags") or []))
            console.print(
                f"  • `{sample['message_id']}` -> {sample['title']} "
                f"[{sample['category']}, {sample['confidence']:.2f}] {tags}"
            )

    if stats.dry_run:
        console.print(
            "[dim]dry-run: предложения не публиковались; кэш и учёт токенов сохранены в БД[/dim]"
        )
