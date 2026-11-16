"""Схема ответа модели: нормализация и защита от неверных значений."""

from __future__ import annotations

import pytest

from kbcollector.llm.schema import (
    MAX_TITLE_LENGTH,
    EnrichmentError,
    normalize_tags,
    parse_enrichment,
    sanitize_title,
)


def test_valid_payload_is_normalized() -> None:
    raw = (
        '{"title": "  **Настройка n8n** ", '
        '"category": "n8n", "tags": ["N8N", " #Webhook ", "webhook"], '
        '"confidence": 0.82, "language": "RU"}'
    )

    result = parse_enrichment(raw, default_rubricator_with_topics())

    assert result.title == "Настройка n8n"
    assert result.category == "n8n"
    assert result.tags == ("n8n", "webhook")
    assert result.confidence == pytest.approx(0.82)
    assert result.language == "ru"


def test_unknown_category_falls_back_to_inbox() -> None:
    raw = '{"title": "T", "category": "Космос", "tags": [], "confidence": 0.9}'

    assert parse_enrichment(raw, default_rubricator_with_topics()).category == "Inbox"


def test_low_confidence_falls_back_to_inbox() -> None:
    raw = '{"title": "T", "category": "n8n", "tags": [], "confidence": 0.2}'

    assert parse_enrichment(raw, default_rubricator_with_topics()).category == "Inbox"


def test_missing_title_is_rejected() -> None:
    with pytest.raises(EnrichmentError):
        parse_enrichment('{"title": "  ", "category": "n8n"}', default_rubricator_with_topics())


def test_non_json_is_rejected() -> None:
    with pytest.raises(EnrichmentError):
        parse_enrichment("не json", default_rubricator_with_topics())


def test_summary_is_not_part_of_schema() -> None:
    raw = '{"title": "T", "category": "n8n", "summary": "лишнее"}'

    result = parse_enrichment(raw, default_rubricator_with_topics())

    assert not hasattr(result, "summary")


def test_title_is_cleaned_and_truncated() -> None:
    assert sanitize_title("# Заголовок\nв две строки") == "Заголовок в две строки"
    assert len(sanitize_title("я" * 300)) <= MAX_TITLE_LENGTH


def test_tags_are_limited_and_deduplicated() -> None:
    tags = normalize_tags([f"тег {index}" for index in range(10)])

    assert len(tags) == 6
    assert tags[0] == "тег-0"


def default_rubricator_with_topics():
    from kbcollector.classification.rubricator import parse_rubricator

    return parse_rubricator({"default": "Inbox", "categories": ["Inbox", "n8n", "Нейросети"]})
