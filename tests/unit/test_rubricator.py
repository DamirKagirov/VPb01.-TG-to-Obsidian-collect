"""Рубрикатор: загрузка, валидация, классификация без LLM."""

from __future__ import annotations

from pathlib import Path

import pytest

from kbcollector.classification.rubricator import (
    MAX_CATEGORY_DEPTH,
    RubricatorError,
    classify,
    default_rubricator,
    load_rubricator,
    parse_rubricator,
)


def test_default_rubricator_is_inbox_only() -> None:
    rubricator = default_rubricator()

    assert rubricator.default == "Inbox"
    assert rubricator.rules == ()
    assert classify("любой текст", rubricator) == "Inbox"


def test_rules_match_case_insensitively_and_first_wins() -> None:
    rubricator = parse_rubricator(
        {
            "default": "Inbox",
            "rules": [
                {"category": "Разработка/Python", "any": ["Python", "asyncio"]},
                {"category": "Автоматизация/n8n", "any": ["n8n"]},
            ],
        }
    )

    assert classify("Про PYTHON и asyncio", rubricator) == "Разработка/Python"
    assert classify("собираю n8n воркфлоу", rubricator) == "Автоматизация/n8n"
    assert classify("просто текст", rubricator) == "Inbox"


def test_category_depth_is_limited() -> None:
    too_deep = "/".join(["a", "b", "c"])

    with pytest.raises(RubricatorError, match=f"больше {MAX_CATEGORY_DEPTH}"):
        parse_rubricator({"rules": [{"category": too_deep, "any": ["x"]}]})


@pytest.mark.parametrize("category", ["", " ", "/", "a//b", "../etc", "плохо:имя"])
def test_invalid_categories_are_rejected(category: str) -> None:
    with pytest.raises(RubricatorError):
        parse_rubricator({"rules": [{"category": category, "any": ["x"]}]})


def test_duplicate_categories_are_rejected() -> None:
    with pytest.raises(RubricatorError, match="дублируется"):
        parse_rubricator(
            {
                "rules": [
                    {"category": "Inbox", "any": ["a"]},
                    {"category": "Inbox", "any": ["b"]},
                ]
            }
        )


def test_empty_keywords_are_rejected() -> None:
    with pytest.raises(RubricatorError, match="any"):
        parse_rubricator({"rules": [{"category": "X", "any": []}]})


def test_loads_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "rubricator.yaml"
    path.write_text(
        "version: 1\ndefault: Inbox\nrules:\n  - category: Разработка/Python\n    any: [python]\n",
        encoding="utf-8",
    )

    rubricator = load_rubricator(path)

    assert rubricator.categories == ("Inbox", "Разработка/Python")


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(RubricatorError, match="не найден"):
        load_rubricator(tmp_path / "absent.yaml")


def test_project_rubricator_is_valid() -> None:
    project_root = Path(__file__).resolve().parents[2]

    rubricator = load_rubricator(project_root / "config" / "rubricator.yaml")

    assert rubricator.default == "Inbox"
