"""Команды kbcollector.

Реализованы только те команды, которые действительно работают.
Остальные команды из ТЗ §16 появятся вместе с соответствующими модулями.
"""

from __future__ import annotations

import json
import platform

import typer
from rich.table import Table

from kbcollector import __version__
from kbcollector.cli.commands.enrich import enrich
from kbcollector.cli.commands.groups import groups
from kbcollector.cli.commands.import_html import import_html
from kbcollector.cli.runtime import console, load_settings
from kbcollector.diagnostics.preflight import Check, Severity, has_failures, run_preflight
from kbcollector.logging import configure_logging

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Telegram-коллекции → Markdown → S3 → Obsidian.",
)

_SEVERITY_STYLE: dict[Severity, str] = {
    Severity.OK: "green",
    Severity.WARN: "yellow",
    Severity.FAIL: "red",
}


@app.callback()
def _bootstrap(
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="Уровень логирования (по умолчанию KBC_LOG_LEVEL).",
    ),
) -> None:
    """Инициализация, общая для всех команд."""
    settings = load_settings()
    configure_logging(log_level or settings.log_level, json_logs=settings.log_json)


@app.command()
def version() -> None:
    """Показать версию приложения и окружения."""
    console.print(f"kbcollector {__version__}")
    console.print(f"Python {platform.python_version()} ({platform.system()} {platform.release()})")


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Вывести результат в JSON."),
    quiet: bool = typer.Option(False, "--quiet", help="Только код возврата, без вывода."),
) -> None:
    """Проверить конфигурацию и окружение. Внешние сервисы не вызываются."""
    checks: list[Check] = run_preflight(load_settings())

    if as_json:
        payload = [check.to_dict() for check in checks]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not quiet:
        _render_checks(checks)

    if has_failures(checks):
        if not quiet and not as_json:
            failed = sum(1 for check in checks if check.severity is Severity.FAIL)
            console.print(f"[red]Провалено проверок: {failed}[/red]")
        raise typer.Exit(code=1)


app.command("import-html")(import_html)
app.command("groups")(groups)
app.command("enrich")(enrich)


def _render_checks(checks: list[Check]) -> None:
    table = Table(title="kbcollector doctor", header_style="bold", expand=False)
    table.add_column("Проверка", no_wrap=True)
    table.add_column("Статус", no_wrap=True)
    table.add_column("Детали", overflow="fold")

    for check in checks:
        detail = check.detail
        if check.hint:
            detail = f"{detail}\n[dim]{check.hint}[/dim]"
        style = _SEVERITY_STYLE[check.severity]
        table.add_row(check.name, f"[{style}]{check.severity}[/]", detail)

    console.print(table)


def main() -> None:
    """Точка входа консольного скрипта."""
    app()
