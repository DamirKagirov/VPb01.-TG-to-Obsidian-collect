"""Загрузка версионируемых промптов и рендеринг входа.

Промпт — Markdown-файл с секциями `## system` и `## user`. Подстановка
выполняется заменой токенов, а не `str.format`, чтобы фигурные скобки
в тексте сообщения не ломали шаблон.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SYSTEM_MARKER = "## system"
USER_MARKER = "## user"


class PromptError(ValueError):
    """Промпт отсутствует или не содержит обязательных секций."""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    version: str
    system: str
    user_template: str


def load_prompt(path: Path, *, version: str) -> PromptTemplate:
    if not path.is_file():
        msg = f"файл промпта не найден: {path}"
        raise PromptError(msg)
    raw = path.read_text(encoding="utf-8")
    if SYSTEM_MARKER not in raw or USER_MARKER not in raw:
        msg = f"в промпте {path} должны быть секции '## system' и '## user'"
        raise PromptError(msg)

    _, after_system = raw.split(SYSTEM_MARKER, 1)
    system, user_template = after_system.split(USER_MARKER, 1)
    system_text = system.strip()
    user_text = user_template.strip()
    if not system_text or not user_text:
        msg = f"пустая секция промпта: {path}"
        raise PromptError(msg)
    return PromptTemplate(version=version, system=system_text, user_template=user_text)


def render_user(
    template: PromptTemplate,
    *,
    categories: tuple[str, ...],
    sent_at: datetime,
    forward_author: str | None,
    text: str,
) -> str:
    forward_block = f"Переслано из: {forward_author}\n" if forward_author else ""
    rendered = template.user_template
    replacements = {
        "{categories}": ", ".join(categories),
        "{date}": sent_at.strftime("%Y-%m-%d %H:%M"),
        "{forward}": forward_block,
        "{text}": text,
    }
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered
