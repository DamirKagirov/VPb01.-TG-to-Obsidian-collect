"""Спайк LLM: проверка endpoint, ключа и доступности модели без платных запросов.

Делает только `GET /models` (OpenAI-совместимый метод, не тарифицируется)
и сверяет настроенную модель со списком доступных. Чат-запрос намеренно
не выполняется: спайк не должен тратить бюджет (ТЗ §12).

Запуск:
    uv run python spikes/llm_probe.py
"""

from __future__ import annotations

import json
import sys

import httpx

from kbcollector.config import Settings


def main() -> int:
    settings = Settings()
    if not settings.llm_configured or settings.llm_api_key is None:
        print("LLM не настроен: нужны KBC_LLM_BASE_URL, KBC_LLM_MODEL, KBC_LLM_API_KEY")
        return 2

    url = f"{settings.llm_base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"}

    try:
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        print(f"Сетевая ошибка: {type(exc).__name__}: {exc}")
        return 1

    print(f"GET {url} -> HTTP {response.status_code}")
    if response.status_code != httpx.codes.OK:
        print(f"Тело ответа: {response.text[:300]}")
        return 1

    payload = response.json()
    models = [item.get("id") for item in payload.get("data", [])]
    configured = settings.llm_model

    print(
        json.dumps(
            {"configured_model": configured, "available_models": models},
            ensure_ascii=False,
            indent=2,
        )
    )
    if configured in models:
        print(f"\nМодель {configured!r} доступна по этому ключу.")
        return 0

    print(f"\nМодель {configured!r} ОТСУТСТВУЕТ в списке доступных — проверьте KBC_LLM_MODEL.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
