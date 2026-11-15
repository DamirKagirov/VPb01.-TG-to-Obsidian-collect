"""Очистка текста перед записью в Markdown.

Убираются управляющие символы (кроме перевода строки и табуляции), нормализуются
переводы строк и пустые строки. HTML из внешнего источника считается недоверенным,
поэтому опасная разметка не переносится: парсер конвертирует её в Markdown сам.
"""

from __future__ import annotations

import re

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_BLANK_LINES = 2


def sanitize_text(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _CONTROL_CHARS.sub("", cleaned)

    result: list[str] = []
    blank_run = 0
    for line in cleaned.split("\n"):
        line = line.rstrip()
        if line:
            blank_run = 0
        else:
            blank_run += 1
            if blank_run > _MAX_BLANK_LINES:
                continue
        result.append(line)

    return "\n".join(result).strip()
