"""HTTP-адаптер: успешный ответ, ошибки, снятие Markdown-обёртки."""

from __future__ import annotations

import httpx
import pytest
import respx

from kbcollector.llm.openai_compat import (
    LLMError,
    OpenAICompatibleProvider,
    parse_json_object,
)

URL = "https://llm.test/chat/completions"


def make_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="https://llm.test", api_key="secret-key", model="test-model", timeout=5
    )


@respx.mock
def test_successful_completion_returns_usage() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": '{"title": "T"}'}}],
                "usage": {"prompt_tokens": 321, "completion_tokens": 45},
            },
        )
    )

    result = make_provider().complete_json(system="s", user="u")

    assert result.content == '{"title": "T"}'
    assert result.input_tokens == 321
    assert result.output_tokens == 45
    assert result.model == "test-model"
    assert route.called
    sent = route.calls[0].request
    assert b'"response_format"' in sent.content


@respx.mock
def test_markdown_fences_are_removed() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '```json\n{"title": "T"}\n```'}}],
                "usage": {},
            },
        )
    )

    assert make_provider().complete_json(system="s", user="u").content == '{"title": "T"}'


@respx.mock
def test_client_error_is_reported() -> None:
    respx.post(URL).mock(return_value=httpx.Response(400, text='{"error": "bad request"}'))

    with pytest.raises(LLMError, match="HTTP 400"):
        make_provider().complete_json(system="s", user="u")


@respx.mock
def test_retry_on_429_then_success() -> None:
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(429, text="rate limited"),
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}], "usage": {}},
        ),
    ]

    result = make_provider().complete_json(system="s", user="u")

    assert result.content == "{}"
    assert route.call_count == 2


def test_parse_json_object_rejects_non_object() -> None:
    with pytest.raises(LLMError):
        parse_json_object("[1, 2]")
    with pytest.raises(LLMError):
        parse_json_object("не json")
    assert parse_json_object('{"a": 1}') == '{"a": 1}'
