"""Схема и нормализация ответа модели.

Правила (план LLM §4): заголовок очищается от Markdown, категория проверяется
по рубрикатору, теги нормализуются, неуверенность ведёт в `Inbox`.
`summary` намеренно отсутствует — решение пользователя от 2026-11-16.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from kbcollector.classification.rubricator import Rubricator

MAX_TITLE_LENGTH = 80
MAX_TAGS = 6
CONFIDENCE_THRESHOLD = 0.5

_MARKDOWN_CHARS = re.compile(r"[#*_`>\[\]]+")
_WHITESPACE = re.compile(r"\s+")


class EnrichmentError(ValueError):
    """Ответ модели не соответствует схеме или пуст."""


class RawEnrichment(BaseModel):
    """Сырой ответ модели (лишние поля игнорируются)."""

    model_config = ConfigDict(extra="ignore")

    title: str
    category: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    language: str | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_must_be_list(cls, value: object) -> object:
        if value is None:
            return []
        return value


@dataclass(frozen=True, slots=True)
class Enrichment:
    title: str
    category: str
    tags: tuple[str, ...]
    confidence: float
    language: str | None


def sanitize_title(value: str) -> str:
    cleaned = _MARKDOWN_CHARS.sub("", value)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().strip("\"'«»")
    cleaned = cleaned.rstrip(".")
    if len(cleaned) > MAX_TITLE_LENGTH:
        cleaned = cleaned[: MAX_TITLE_LENGTH - 1].rstrip() + "…"
    return cleaned


def normalize_tags(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        tag = value.strip().lower().lstrip("#").strip()
        tag = tag.replace(" ", "-")
        if not tag or len(tag) > 40 or tag in result:
            continue
        result.append(tag)
        if len(result) >= MAX_TAGS:
            break
    return tuple(result)


def parse_enrichment(raw_json: str, rubricator: Rubricator) -> Enrichment:
    try:
        payload = RawEnrichment.model_validate_json(raw_json)
    except ValidationError as exc:
        msg = f"ответ модели не соответствует схеме: {exc.error_count()} ошибок"
        raise EnrichmentError(msg) from exc

    title = sanitize_title(payload.title)
    if not title:
        msg = "модель вернула пустой заголовок"
        raise EnrichmentError(msg)

    confidence = min(max(float(payload.confidence), 0.0), 1.0)
    category = payload.category.strip().strip("/")
    if category not in rubricator.categories:
        category = rubricator.default
    if confidence < CONFIDENCE_THRESHOLD:
        category = rubricator.default

    language = (payload.language or "").strip().lower()[:8] or None
    return Enrichment(
        title=title,
        category=category,
        tags=normalize_tags(payload.tags),
        confidence=confidence,
        language=language,
    )


def as_json(enrichment: Enrichment) -> str:
    payload = RawEnrichment(
        title=enrichment.title,
        category=enrichment.category,
        tags=list(enrichment.tags),
        confidence=enrichment.confidence,
        language=enrichment.language,
    )
    return payload.model_dump_json()
