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

import argparse
import json
import re
import sys
from typing import Any

import httpx

from kbcollector.config import Settings

_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")


def _mask(text: str) -> str:
    return _TOKEN_RE.sub("<redacted>", text)


def _call(client: httpx.Client, method: str, **params: Any) -> Any:  # ответы Bot API разной формы
    response = client.post(method, json=params or None)
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"{method}: {payload.get('description', 'unknown error')}")
    return payload.get("result")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--peek",
        action="store_true",
        help="показать chat_id из последнего update (update НЕ подтверждается)",
    )
    parser.add_argument(
        "--chats",
        action="store_true",
        help="запросить getChat для id из allowlist",
    )
    args = parser.parse_args()

    settings = Settings()
    if settings.telegram_bot_token is None:
        print("KBC_TELEGRAM_BOT_TOKEN не задан")
        return 2

    token = settings.telegram_bot_token.get_secret_value()
    base_url = f"https://api.telegram.org/bot{token}"

    try:
        with httpx.Client(base_url=base_url, timeout=25) as client:
            me = _call(client, "getMe")
            webhook = _call(client, "getWebhookInfo")
            peeked = (
                _call(client, "getUpdates", offset=-1, limit=1, timeout=0) if args.peek else None
            )
            chats: list[dict[str, Any]] = []
            if args.chats:
                for chat_id in {
                    *settings.telegram_allowed_chat_ids,
                    *settings.telegram_allowed_user_ids,
                }:
                    try:
                        info = _call(client, "getChat", chat_id=chat_id)
                        chats.append(
                            {
                                "requested_id": chat_id,
                                "type": info.get("type"),
                                "id": info.get("id"),
                                "title": info.get("title"),
                                "username": info.get("username"),
                                "first_name": info.get("first_name"),
                            }
                        )
                    except RuntimeError as exc:
                        chats.append({"requested_id": chat_id, "error": _mask(str(exc))})
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

    if peeked is not None:
        if not peeked:
            report["peek"] = {
                "updates": 0,
                "hint": "pending updates нет: отправьте боту сообщение и повторите",
            }
        else:
            update = peeked[-1]
            message = (
                update.get("message")
                or update.get("edited_message")
                or update.get("channel_post")
                or {}
            )
            chat = message.get("chat", {})
            sender = message.get("from", {})
            report["peek"] = {
                "update_id": update.get("update_id"),
                "update_keys": sorted(update.keys()),
                "chat_id": chat.get("id"),
                "chat_type": chat.get("type"),
                "chat_title": chat.get("title") or chat.get("first_name"),
                "from_id": sender.get("id"),
                "message_id": message.get("message_id"),
                "date": message.get("date"),
                "has_text": bool(message.get("text") or message.get("caption")),
            }

    if chats:
        report["chats"] = chats

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
