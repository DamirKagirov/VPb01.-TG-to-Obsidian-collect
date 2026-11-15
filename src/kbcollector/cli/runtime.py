"""Общие зависимости CLI: консоль и загрузка настроек."""

from __future__ import annotations

import typer
from pydantic import ValidationError
from rich.console import Console

from kbcollector.config import Settings

console = Console()


def load_settings() -> Settings:
    """Читает настройки и печатает понятную ошибку вместо трейсбека."""
    try:
        return Settings()
    except ValidationError as exc:
        console.print("[red]Некорректная конфигурация:[/red]")
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            console.print(f"  • KBC_{location.upper()}: {error['msg']}")
        raise typer.Exit(code=2) from exc
