"""YAML front matter для Obsidian.

Соблюдаются требования ТЗ §11.2–11.3: `kb_id` в кавычках, кириллица без
экранирования (`allow_unicode`), порядок ключей сохраняется, значения
с двоеточиями и кавычками экранируются средствами PyYAML.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

QUOTED_KEYS = frozenset({"kb_id"})


class _Quoted(str):
    """Строка, которая всегда сериализуется в двойных кавычках."""


def _represent_quoted(dumper: yaml.SafeDumper, data: _Quoted) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


yaml.SafeDumper.add_representer(_Quoted, _represent_quoted)


class FrontmatterError(ValueError):
    """Front matter невозможно сериализовать."""


def render_frontmatter(fields: Mapping[str, Any]) -> str:
    prepared: dict[str, Any] = {
        key: _Quoted(value) if key in QUOTED_KEYS and isinstance(value, str) else value
        for key, value in fields.items()
    }
    try:
        dumped = yaml.safe_dump(
            prepared,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=10_000,
        )
    except yaml.YAMLError as exc:  # pragma: no cover - защита от несериализуемых значений
        msg = f"не удалось сериализовать front matter: {exc}"
        raise FrontmatterError(msg) from exc
    return f"---\n{dumped}---\n"
