"""Тесты маскирования секретов и обрезки текстов в логах."""

from __future__ import annotations

from kbcollector.logging import redact_processor

TOKEN = "123456789:AAH-secret-token-value-1234567890"


def _process(**event: object) -> dict[str, object]:
    return redact_processor(None, "info", dict(event))


def test_bot_token_in_url_is_masked() -> None:
    event = _process(url=f"https://api.telegram.org/bot{TOKEN}/getUpdates")

    assert TOKEN not in str(event["url"])
    assert event["url"] == "https://api.telegram.org/bot<redacted>/getUpdates"


def test_sensitive_keys_are_fully_redacted() -> None:
    event = _process(token="abc", api_key="xyz", Authorization="Bearer q", attempts=3)

    assert event["token"] == "<redacted>"
    assert event["api_key"] == "<redacted>"
    assert event["Authorization"] == "<redacted>"
    assert event["attempts"] == 3


def test_long_text_is_truncated_to_preview() -> None:
    text = "x" * 500

    event = _process(text=text)

    rendered = str(event["text"])
    assert rendered.startswith("x" * 10)
    assert "500 символов" in rendered
    assert len(rendered) < 120


def test_short_text_is_kept_as_is() -> None:
    assert _process(text="привет")["text"] == "привет"


def test_nested_structures_are_sanitized() -> None:
    event = _process(
        request={"headers": {"Authorization": "Bearer secret"}, "url": "https://example.org"},
        items=[{"api_key": "k"}, "plain"],
    )

    assert event["request"] == {
        "headers": {"Authorization": "<redacted>"},
        "url": "https://example.org",
    }
    assert event["items"] == [{"api_key": "<redacted>"}, "plain"]


def test_non_string_values_pass_through() -> None:
    assert _process(count=5, ratio=0.5, flag=True, empty=None) == {
        "count": 5,
        "ratio": 0.5,
        "flag": True,
        "empty": None,
    }
