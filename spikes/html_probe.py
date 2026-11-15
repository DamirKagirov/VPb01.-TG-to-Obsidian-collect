"""Спайк S3: инвентаризация структуры HTML-экспорта Telegram Desktop.

Не парсер, а исследовательский зонд: собирает статистику по контейнерам,
датам, reply/forward, коду, ссылкам и вложениям, чтобы зафиксировать
фактический формат в docs/import.md и подготовить fixtures.

Запуск:
    uv run python spikes/html_probe.py mydata/messages.html --json docs/reports/html-probe.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

MAX_SAMPLES = 2
SNIPPET_LIMIT = 600
WIDE_LIMIT = 2500


def _snippet(node: Tag) -> str:
    rendered = str(node)
    return rendered[:SNIPPET_LIMIT] + ("…" if len(rendered) > SNIPPET_LIMIT else "")


def _snippet_wide(node: Tag) -> str:
    rendered = str(node)
    return rendered[:WIDE_LIMIT] + ("…" if len(rendered) > WIDE_LIMIT else "")


def _classes(node: Tag) -> list[str]:
    value = node.get("class", [])
    return list(value) if isinstance(value, list) else str(value).split()


def _collect_samples(soup: BeautifulSoup, selector: str, buckets: dict[str, list[str]]) -> int:
    nodes = soup.select(selector)
    if nodes:
        buckets.setdefault(selector, [])
        for node in nodes[:MAX_SAMPLES]:
            buckets[selector].append(_snippet(node))
    return len(nodes)


def probe(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw, "lxml")

    messages = soup.select("div.message")
    kinds: Counter[str] = Counter()
    for message in messages:
        classes = _classes(message)
        kinds["joined" if "joined" in classes else ",".join(classes)] += 1

    # Даты: блок details с атрибутом title (Unix timestamp) и текстом.
    dates = soup.select("div.message div.date.details")
    date_samples = []
    for node in dates[:3]:
        date_samples.append(
            {
                "classes": _classes(node),
                "title": node.get("title"),
                "text": node.get_text(" ", strip=True),
            }
        )

    # Авторы: блок from_name и его атрибуты.
    authors = soup.select("div.from_name")
    author_samples = [
        {"text": node.get_text(" ", strip=True), "attrs": dict(node.attrs)} for node in authors[:3]
    ]

    # Вложения и медиа: какие обёртки реально встречаются.
    attachment_selectors = [
        "a.photo_wrap",
        "a.video_file_wrap",
        "a.audio_file",
        "a.sticker_wrap",
        "a.document",
        "div.document_wrap",
        "div.media_wrap",
        "div.voice_message",
        "div.video_message",
        "div.media_only",
        "img.emoji",
    ]
    attachment_counts = {selector: len(soup.select(selector)) for selector in attachment_selectors}

    samples: dict[str, list[str]] = {}
    counts: dict[str, int] = {
        "div.message": len(messages),
        "div.message.joined": kinds.get("joined", 0),
        "div.message.service": len(soup.select("div.message.service")),
        "div.date.details": len(dates),
        "div.from_name": len(authors),
        **{f"attachment:{selector}": value for selector, value in attachment_counts.items()},
        "reply_to": _collect_samples(soup, "div.reply_to", samples),
        "forwarded": _collect_samples(soup, "div.forwarded", samples),
        "forwarded_body": _collect_samples(soup, "div.forwarded.body", samples),
        "code_inline": _collect_samples(soup, "code", samples),
        "code_block": _collect_samples(soup, "pre", samples),
        "blockquote": _collect_samples(soup, "blockquote", samples),
        "text_link": _collect_samples(soup, "a", samples),
        "service_body": _collect_samples(soup, "div.message.service div.body", samples),
        "media_title": _collect_samples(soup, "div.title", samples),
        "media_description": _collect_samples(soup, "div.description", samples),
        "kbd": _collect_samples(soup, "span.kbd", samples),
        "spoiler": _collect_samples(soup, "span.spoiler", samples),
        "tg_emoji": _collect_samples(soup, "i.emoji", samples),
    }

    message_ids = []
    for message in messages[:5]:
        message_ids.append({"id": message.get("id"), "classes": _classes(message)})

    # Полные примеры: обычное сообщение, сообщение с медиа, служебное.
    full_samples: dict[str, str] = {}
    first_plain = soup.select_one("div.message.default.clearfix:not(.joined)")
    if first_plain:
        full_samples["plain_message"] = _snippet_wide(first_plain)
    first_joined = soup.select_one("div.message.default.clearfix.joined")
    if first_joined:
        full_samples["joined_message"] = _snippet_wide(first_joined)
    first_media = soup.select_one("div.message.default.clearfix:has(a.photo_wrap)")
    if first_media:
        full_samples["media_message"] = _snippet_wide(first_media)
    first_reply = soup.select_one("div.message:has(div.reply_to)")
    if first_reply:
        full_samples["reply_message"] = _snippet_wide(first_reply)
    first_forward = soup.select_one("div.message:has(div.forwarded.body)")
    if first_forward:
        full_samples["forwarded_message"] = _snippet_wide(first_forward)
    first_service = soup.select_one("div.message.service")
    if first_service:
        full_samples["service_message"] = _snippet_wide(first_service)

    # Служебные сообщения: что именно в них встречается.
    service_texts: Counter[str] = Counter()
    for node in soup.select("div.message.service div.body"):
        text = node.get_text(" ", strip=True)
        if text and not text[:1].isdigit():
            service_texts[text[:120]] += 1

    # Виды ссылок: абсолютные, якоря, пустые с onclick (команды бота).
    link_kinds: Counter[str] = Counter()
    for link in soup.select("a[href]"):
        href = str(link.get("href", "")).strip()
        onclick = str(link.get("onclick", ""))
        if href.startswith(("http://", "https://")):
            link_kinds["absolute"] += 1
        elif href.startswith("#go_to_message"):
            link_kinds["reply_anchor"] += 1
        elif not href and onclick:
            link_kinds["empty_href_onclick"] += 1
        elif href.startswith(("photos/", "files/", "video_files/", "stickers/")):
            link_kinds["attachment"] += 1
        else:
            link_kinds["other"] += 1

    # Глубина вложенности пересылок: forward внутри forward.
    max_forward_depth = 0
    for node in soup.select("div.forwarded.body"):
        depth = 1
        parent = node.parent
        while parent is not None:
            if isinstance(parent, Tag) and "forwarded" in _classes(parent):
                depth += 1
            parent = parent.parent
        max_forward_depth = max(max_forward_depth, depth)

    date_attrs: Counter[str] = Counter()
    for node in dates:
        date_attrs.update(node.attrs.keys())

    hrefs = Counter()
    for link in soup.select("a[href]"):
        href = str(link.get("href", ""))
        if href.startswith(("photos/", "files/", "video_files/", "stickers/")):
            hrefs[href.split("/")[0]] += 1

    return {
        "file": str(path),
        "generated_at": datetime.now(UTC).isoformat(),
        "encoding_declared": "utf-8",
        "counts": counts,
        "message_kind_breakdown": dict(kinds),
        "date_samples": date_samples,
        "date_attribute_keys": dict(date_attrs),
        "author_samples": author_samples,
        "attachment_href_roots": dict(hrefs),
        "message_id_samples": message_ids,
        "service_texts": dict(service_texts.most_common(10)),
        "link_kinds": dict(link_kinds),
        "mentions": len(soup.select("a.mention")),
        "hashtags": len(soup.select("a.hashtag")),
        "max_forward_depth": max_forward_depth,
        "samples": samples,
        "full_samples": full_samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path, help="путь к messages.html")
    parser.add_argument("--json", dest="json_path", type=Path, default=None)
    args = parser.parse_args()

    if not args.html.exists():
        print(f"файл не найден: {args.html}")
        return 2

    report = probe(args.html)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"samples", "full_samples"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n--- samples ---")
    for selector, items in report["samples"].items():
        print(f"\n### {selector}")
        for item in items:
            print(item)

    print("\n--- full samples ---")
    for name, item in report["full_samples"].items():
        print(f"\n### {name}")
        print(item)

    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON: {args.json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
