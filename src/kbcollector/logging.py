"""Структурное логирование с маскированием секретов.

Правила (ТЗ §16, §17):
* логи идут в stderr, чтобы не смешиваться с выводом CLI;
* JSON по умолчанию — для Docker; читаемый вывод — для локальной отладки;
* токен бота в URL и значения полей с секретами маскируются всегда;
* полный текст сообщений в логи по умолчанию не попадает — только превью.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any, cast

import structlog

# https://api.telegram.org/bot<id>:<secret>/method
_BOT_TOKEN_RE = re.compile(r"(?<=bot)\d{6,}:[A-Za-z0-9_-]{20,}")
_SENSITIVE_KEY_RE = re.compile(r"(token|secret|password|api[_-]?key|authorization)", re.IGNORECASE)
_TEXT_KEYS = frozenset({"text", "caption", "content", "body", "message", "summary"})
_TEXT_PREVIEW_LIMIT = 80


def _sanitize_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY_RE.search(key):
        return "<redacted>"
    if isinstance(value, str):
        masked = _BOT_TOKEN_RE.sub("<redacted>", value)
        if key in _TEXT_KEYS and len(masked) > _TEXT_PREVIEW_LIMIT:
            return f"{masked[:_TEXT_PREVIEW_LIMIT]}… <{len(masked)} символов>"
        return masked
    if isinstance(value, Mapping):
        return {str(k): _sanitize_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_sanitize_value(key, item) for item in value]
    return value


def redact_processor(
    _logger: object,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Процессор structlog: маскирует секреты и обрезает длинные тексты."""
    return {key: _sanitize_value(key, value) for key, value in event_dict.items()}


def configure_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    """Настраивает structlog и stdlib logging для текущего процесса."""
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        msg = f"Неизвестный уровень логирования: {level!r}"
        raise ValueError(msg)

    renderer: structlog.types.Processor
    if json_logs:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        level=numeric_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    """Логгер с именем модуля: `get_logger(__name__)`."""
    return cast("structlog.typing.FilteringBoundLogger", structlog.get_logger(name))
