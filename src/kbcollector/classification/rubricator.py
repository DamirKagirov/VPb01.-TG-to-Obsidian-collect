"""Рубрикатор vault: загрузка, валидация, классификация без LLM.

Правила (ТЗ §11.1, §12):
* категории задаются только в конфигурации, модель не создаёт новые;
* глубина иерархии ограничена двумя уровнями;
* всё неопознанное попадает в `default` (`Inbox`), а не в произвольную папку.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MAX_CATEGORY_DEPTH = 2
_FORBIDDEN = set('<>:"\\|?*\x00')


class RubricatorError(ValueError):
    """Рубрикатор отсутствует или некорректен."""


@dataclass(frozen=True, slots=True)
class Rule:
    category: str
    any_keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Rubricator:
    version: int
    default: str
    rules: tuple[Rule, ...]
    # Явный список допустимых категорий (например, темы для LLM).
    # Если пуст — используются default и категории правил.
    allowed: tuple[str, ...] = ()

    @property
    def categories(self) -> tuple[str, ...]:
        if self.allowed:
            return self.allowed
        return (self.default, *(rule.category for rule in self.rules))


def _validate_category(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{where}: категория должна быть непустой строкой"
        raise RubricatorError(msg)
    category = value.strip().strip("/")
    if any(char in _FORBIDDEN for char in category):
        msg = f"{where}: недопустимые символы в категории {value!r}"
        raise RubricatorError(msg)
    parts = [part.strip() for part in category.split("/")]
    if any(not part or part in {".", ".."} for part in parts):
        msg = f"{where}: некорректный путь категории {value!r}"
        raise RubricatorError(msg)
    if len(parts) > MAX_CATEGORY_DEPTH:
        msg = f"{where}: глубина категории больше {MAX_CATEGORY_DEPTH}: {value!r}"
        raise RubricatorError(msg)
    return "/".join(parts)


def parse_rubricator(data: dict[str, Any]) -> Rubricator:
    version = data.get("version", 1)
    if not isinstance(version, int) or version < 1:
        msg = "rubricator: version должна быть целым числом >= 1"
        raise RubricatorError(msg)

    default = _validate_category(data.get("default", "Inbox"), where="rubricator.default")

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        msg = "rubricator.rules должна быть списком"
        raise RubricatorError(msg)

    rules: list[Rule] = []
    seen: set[str] = set()
    for index, raw_rule in enumerate(raw_rules):
        where = f"rubricator.rules[{index}]"
        if not isinstance(raw_rule, dict):
            msg = f"{where}: ожидается объект"
            raise RubricatorError(msg)
        category = _validate_category(raw_rule.get("category"), where=where)
        if category in seen:
            msg = f"{where}: категория дублируется: {category}"
            raise RubricatorError(msg)
        seen.add(category)

        keywords_raw = raw_rule.get("any", [])
        if (
            not isinstance(keywords_raw, list)
            or not keywords_raw
            or not all(isinstance(item, str) and item.strip() for item in keywords_raw)
        ):
            msg = f"{where}: any должен быть непустым списком непустых строк"
            raise RubricatorError(msg)
        keywords = tuple(item.strip().casefold() for item in keywords_raw)
        rules.append(Rule(category=category, any_keywords=keywords))

    raw_allowed = data.get("categories", [])
    allowed: list[str] = []
    if raw_allowed:
        if not isinstance(raw_allowed, list):
            msg = "rubricator.categories должен быть списком"
            raise RubricatorError(msg)
        for index, item in enumerate(raw_allowed):
            category = _validate_category(item, where=f"rubricator.categories[{index}]")
            if category in allowed:
                msg = f"rubricator.categories: дублируется {category}"
                raise RubricatorError(msg)
            allowed.append(category)
        if default not in allowed:
            allowed.insert(0, default)

    return Rubricator(version=version, default=default, rules=tuple(rules), allowed=tuple(allowed))


def load_rubricator(path: Path) -> Rubricator:
    if not path.is_file():
        msg = f"файл рубрикатора не найден: {path}"
        raise RubricatorError(msg)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"не удалось прочитать рубрикатор {path}: {exc}"
        raise RubricatorError(msg) from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        msg = f"rubricator: ожидается YAML-объект, получено {type(loaded).__name__}"
        raise RubricatorError(msg)
    return parse_rubricator(loaded)


def classify(text: str, rubricator: Rubricator) -> str:
    """Категория по правилам; при отсутствии совпадений — `default`."""
    haystack = text.casefold()
    for rule in rubricator.rules:
        if any(keyword in haystack for keyword in rule.any_keywords):
            return rule.category
    return rubricator.default


def default_rubricator() -> Rubricator:
    return Rubricator(version=1, default="Inbox", rules=(), allowed=("Inbox",))
