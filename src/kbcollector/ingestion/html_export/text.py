"""Преобразование HTML-фрагмента сообщения в Markdown-текст.

Правила (ТЗ §8.1, §11.3):
* `<br/>` → перевод строки, `<pre>` → fenced-блок, `<code>` → inline-код;
* `<blockquote>` → цитата, spoiler → `||текст||`;
* внешние ссылки → `[текст](url)`, текстовые гиперссылки сохраняют `href`;
* ссылки-ответы (`#go_to_message…`) не выводятся как ссылки;
* команды бота (`href=""` + `onclick`) не превращаются в битые Markdown-ссылки;
* вложения заменяются маркером, который подставляется после загрузки файла.
"""

from __future__ import annotations

from urllib.parse import unquote

from bs4.element import NavigableString, Tag

from kbcollector.domain.message import ATTACHMENT_TOKEN

_BLOCK_TAGS = frozenset({"p", "div", "li"})


def _classes(node: Tag) -> list[str]:
    raw = node.get("class")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return raw.split()
    return []


def _is_attachment_href(href: str) -> bool:
    return href.startswith(("photos/", "files/", "video_files/", "stickers/"))


def _render_children(node: Tag) -> str:
    return "".join(_render_node(child) for child in node.children)


def _render_link(node: Tag) -> str:
    href = str(node.get("href", "")).strip()
    text = _render_children(node).strip()

    if href.startswith("#go_to_message"):
        return ""
    if href.startswith(("http://", "https://")):
        if not text:
            return href
        return href if text == href else f"[{text}]({href})"
    if _is_attachment_href(href):
        return ATTACHMENT_TOKEN.format(rel_path=unquote(href))
    # Команды бота: href пустой, действие в onclick — ссылку не создаём.
    if text.startswith("/"):
        return f"`{text}`"
    return text


def _render_code(node: Tag) -> str:
    content = node.get_text()
    fence = "``" if "`" in content else "`"
    return f"{fence}{content}{fence}"


def _render_pre(node: Tag) -> str:
    content = _render_children(node).strip("\n")
    return f"\n```\n{content}\n```\n"


def _render_blockquote(node: Tag) -> str:
    inner = _render_children(node).strip()
    quoted = "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
    return f"\n{quoted}\n"


def _render_node(node: object) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""

    name = node.name.lower()
    if name == "br":
        return "\n"
    if name in {"script", "style"}:
        return ""
    if name == "a":
        return _render_link(node)
    if name == "code" and node.parent is not None and node.parent.name == "pre":
        return _render_children(node)
    if name == "code":
        return _render_code(node)
    if name == "pre":
        return _render_pre(node)
    if name == "blockquote":
        return _render_blockquote(node)
    if name == "img":
        return str(node.get("alt", "") or "")
    if name == "span" and "spoiler" in _classes(node):
        return f"||{_render_children(node).strip()}||"
    if name in _BLOCK_TAGS:
        return _render_children(node) + "\n"
    return _render_children(node)


def render_message_text(node: Tag) -> str:
    """Markdown-текст сообщения (без финальной очистки пробелов)."""
    return _render_children(node).replace("\u00a0", " ")
