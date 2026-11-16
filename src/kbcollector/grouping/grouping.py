"""Поиск связанных сообщений по достоверным сигналам (ТЗ §9).

Что считается сигналом:
* продолжение блока (`joined`) — Telegram сам объединил сообщения одного автора;
* ответ (`reply_to_id`) на сообщение из того же экспорта.

Что сигналом **не** считается: близость по времени и общий автор сами по себе.
Альбомы в HTML-экспорте не размечены (`media_group_id` есть только в Bot API),
поэтому этот сигнал здесь отсутствует и фиксируется отдельно.

Группы — только кандидаты: они не применяются автоматически (ТЗ §4.4, §9.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kbcollector.domain.message import ParsedMessage

MAX_GROUP_SIZE = 10
REPLY_CHAIN = "reply_chain"
JOINED_RUN = "joined_run"


@dataclass(frozen=True, slots=True)
class GroupCandidate:
    """Кандидат на объединение: несколько сообщений в одну заметку."""

    kind: str
    message_ids: tuple[int, ...]

    @property
    def primary_id(self) -> int:
        return self.message_ids[0]

    @property
    def reason(self) -> str:
        if self.kind == JOINED_RUN:
            return "сообщения идут подряд в одном блоке автора (joined)"
        return "сообщение является ответом на другое сообщение в этом же чате"


def _joined_runs(messages: Sequence[ParsedMessage]) -> list[GroupCandidate]:
    groups: list[GroupCandidate] = []
    run: list[int] = []
    full = False

    def flush() -> None:
        nonlocal full
        if len(run) > 1:
            groups.append(GroupCandidate(kind=JOINED_RUN, message_ids=tuple(run[:MAX_GROUP_SIZE])))
        run.clear()
        full = False

    for message in messages:
        if message.joined and run:
            if full:
                # Группа уже максимального размера: лишние продолжения не создают
                # новую группу без «якорного» сообщения.
                continue
            run.append(message.message_id)
            full = len(run) >= MAX_GROUP_SIZE
            continue
        flush()
        run.append(message.message_id)
    flush()
    return groups


def _reply_pairs(messages: Sequence[ParsedMessage], used: set[int]) -> list[GroupCandidate]:
    known = {message.message_id for message in messages}
    groups: list[GroupCandidate] = []
    for message in messages:
        target = message.reply_to_id
        if target is None or target not in known:
            continue
        if message.message_id in used or target in used:
            continue
        used.update({target, message.message_id})
        groups.append(GroupCandidate(kind=REPLY_CHAIN, message_ids=(target, message.message_id)))
    return groups


def find_groups(messages: Sequence[ParsedMessage]) -> list[GroupCandidate]:
    """Возвращает непересекающиеся кандидаты в порядке появления сообщений."""
    joined = _joined_runs(messages)
    used = {message_id for group in joined for message_id in group.message_ids}
    replies = _reply_pairs(messages, used)
    return sorted([*joined, *replies], key=lambda group: group.primary_id)


def unsupported_signals() -> dict[str, str]:
    """Сигналы, недоступные в HTML-экспорте — важно для отчёта."""
    return {
        "album": "media_group_id отсутствует в HTML-экспорте (есть только в Bot API)",
        "time_proximity": "не используется: близость по времени не доказывает связь",
    }
