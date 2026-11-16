"""HTTP-адаптер OpenAI-совместимого API (DeepSeek, OpenRouter, Ollama, vLLM).

Особенности:
* только JSON-ответ (`response_format=json_object`), без инструментов и стриминга;
* возвращает фактический `usage`, чтобы стоимость считалась по данным API;
* повторяет запрос при 429/5xx и сетевых ошибках с линейным backoff;
* в сообщениях об ошибках нет ключей и полного текста.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from kbcollector.config import Settings

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BODY_SNIPPET = 200


class LLMError(RuntimeError):
    """Запрос к модели не удался или вернул некорректный ответ."""


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    input_tokens: int
    output_tokens: int
    model: str


def _strip_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class OpenAICompatibleProvider:
    """Минимальный клиент chat/completions."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAICompatibleProvider:
        if not settings.llm_configured or settings.llm_api_key is None:
            msg = "LLM не настроен: нужны KBC_LLM_BASE_URL, KBC_LLM_MODEL и KBC_LLM_API_KEY"
            raise LLMError(msg)
        return cls(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model or "",
            timeout=settings.llm_timeout_seconds,
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 400,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        last_error = "неизвестная ошибка"
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(self._url, headers=self._headers, json=body)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(attempt)
                    continue
                raise LLMError(
                    f"сетевая ошибка после {_MAX_ATTEMPTS} попыток: {last_error}"
                ) from exc

            if response.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS:
                last_error = f"HTTP {response.status_code}"
                time.sleep(attempt)
                continue

            if response.status_code != httpx.codes.OK:
                snippet = response.text[:_BODY_SNIPPET]
                msg = f"HTTP {response.status_code} от модели: {snippet}"
                raise LLMError(msg)

            payload = response.json()
            try:
                content = str(payload["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as exc:
                msg = "в ответе модели нет choices[0].message.content"
                raise LLMError(msg) from exc

            if not content.strip():
                finish = str(payload["choices"][0].get("finish_reason"))
                usage = payload.get("usage") or {}
                msg = (
                    "модель вернула пустой ответ "
                    f"(finish_reason={finish}, completion_tokens={usage.get('completion_tokens')})"
                )
                raise LLMError(msg)

            usage = payload.get("usage") or {}
            return ChatResult(
                content=_strip_fences(content),
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                model=str(payload.get("model") or self._model),
            )

        raise LLMError(last_error)


def parse_json_object(content: str) -> str:
    """Проверяет, что ответ — JSON-объект, и возвращает его компактно."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        snippet = content[:120].replace("\n", " ")
        msg = f"модель вернула не-JSON ({exc.msg}); начало ответа: {snippet!r}"
        raise LLMError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "модель вернула JSON, но не объект"
        raise LLMError(msg)
    return json.dumps(parsed, ensure_ascii=False)
