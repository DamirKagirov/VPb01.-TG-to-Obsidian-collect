"""Спайк S5 (часть 1): проверка токена и состояния webhook без изменения конфигурации.

Выполняет только read-only методы Bot API:
* `getMe` — токен валиден, бот существует;
* `getWebhookInfo` — есть ли webhook, сколько updates ждёт обработки.

Намеренно НЕ вызывает `getUpdates` (не забирает updates и не двигает offset),
`deleteWebhook` и `setWebhook` (ТЗ §7: не удалять webhook автоматически).

Запуск:
    uv run python spikes/telegram_probe.py
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

import httpx

from kbcollector.config import Settings

_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")


def _mask(text: str) -> str:
    return _TOKEN_RE.sub("<redacted>", text)


def _call(client: httpx.Client, method: str) -> dict[str, Any]:
    response = client.post(method)
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"{method}: {payload.get('description', 'unknown error')}")
    result = payload.get("result")
    return result if isinstance(result, dict) else {"result": result}


def main() -> int:
    settings = Settings()
    if settings.telegram_bot_token is None:
        print("KBC_TELEGRAM_BOT_TOKEN не задан")
        return 2

    token = settings.telegram_bot_token.get_secret_value()
    base_url = f"https://api.telegram.org/bot{token}"

    try:
        with httpx.Client(base_url=base_url, timeout=20) as client:
            me = _call(client, "getMe")
            webhook = _call(client, "getWebhookInfo")
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"Ошибка Bot API: {_mask(str(exc))}")
        return 1

    report = {
        "bot": {
            "id": me.get("id"),
            "username": me.get("username"),
            "is_bot": me.get("is_bot"),
            "can_join_groups": me.get("can_join_groups"),
        },
        "webhook": {
            "has_webhook": bool(webhook.get("url")),
            "url_host": None if not webhook.get("url") else httpx.URL(str(webhook["url"])).host,
            "pending_update_count": webhook.get("pending_update_count"),
            "last_error_date": webhook.get("last_error_date"),
            "last_error_message": webhook.get("last_error_message"),
            "allowed_updates": webhook.get("allowed_updates"),
            "max_connections": webhook.get("max_connections"),
        },
        "allowlist": {
            "users": settings.telegram_allowed_user_ids,
            "chats": settings.telegram_allowed_chat_ids,
        },
        "polling_timeout": settings.telegram_poll_timeout,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if report["webhook"]["has_webhook"]:
        print(
            "\nВНИМАНИЕ: у токена активен webhook. Long polling невозможен, "
            "пока webhook не снят; удалять его автоматически приложение не будет."
        )
    else:
        print("\nWebhook не установлен — long polling доступен.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
