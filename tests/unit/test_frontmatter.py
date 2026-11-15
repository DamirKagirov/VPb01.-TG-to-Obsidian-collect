"""Front matter: YAML, кириллица, экранирование."""

from __future__ import annotations

import yaml

from kbcollector.markdown.frontmatter import render_frontmatter


def _parse(rendered: str) -> dict[str, object]:
    assert rendered.startswith("---\n")
    assert rendered.endswith("---\n")
    body = rendered[4:-4]
    parsed = yaml.safe_load(body)
    assert isinstance(parsed, dict)
    return parsed


def test_key_order_is_preserved() -> None:
    rendered = render_frontmatter({"kb_id": "note_1", "title": "T", "tags": []})

    assert rendered.index("kb_id") < rendered.index("title") < rendered.index("tags")


def test_cyrillic_is_not_escaped() -> None:
    parsed = _parse(render_frontmatter({"title": "Полезный материал — тест"}))

    assert parsed["title"] == "Полезный материал — тест"
    assert "\\u" not in render_frontmatter({"title": "кириллица"})


def test_special_characters_roundtrip() -> None:
    title = 'Заголовок: с "кавычками" и # решёткой'

    parsed = _parse(render_frontmatter({"title": title}))

    assert parsed["title"] == title


def test_empty_list_and_numbers() -> None:
    parsed = _parse(render_frontmatter({"tags": [], "message_id": 143053, "schema_version": 1}))

    assert parsed["tags"] == []
    assert parsed["message_id"] == 143053
    assert parsed["schema_version"] == 1
