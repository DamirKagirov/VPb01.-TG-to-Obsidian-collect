"""Группировка: сигналы, отсутствие группировки по времени, предложения."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kbcollector.domain.message import ParsedMessage
from kbcollector.grouping.grouping import JOINED_RUN, REPLY_CHAIN, find_groups
from kbcollector.grouping.proposal import proposal_id, render_proposal
from kbcollector.publication.layout import VaultLayout

BASE = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
LAYOUT = VaultLayout("vault-test")


def message(
    message_id: int,
    *,
    seconds: int = 0,
    joined: bool = False,
    reply_to: int | None = None,
    text: str = "текст",
) -> ParsedMessage:
    return ParsedMessage(
        message_id=message_id,
        sent_at=BASE + timedelta(seconds=seconds),
        author="Автор",
        text=text,
        joined=joined,
        reply_to_id=reply_to,
    )


def test_joined_run_is_one_candidate() -> None:
    messages = [message(1), message(2, seconds=5, joined=True), message(3, seconds=9, joined=True)]

    groups = find_groups(messages)

    assert len(groups) == 1
    assert groups[0].kind == JOINED_RUN
    assert groups[0].message_ids == (1, 2, 3)
    assert groups[0].primary_id == 1


def test_reply_pair_is_one_candidate() -> None:
    messages = [message(10), message(40), message(11, seconds=60, reply_to=10)]

    groups = find_groups(messages)

    assert [(group.kind, group.message_ids) for group in groups] == [(REPLY_CHAIN, (10, 11))]


def test_reply_to_unknown_message_is_ignored() -> None:
    messages = [message(1, reply_to=999)]

    assert find_groups(messages) == []


def test_time_proximity_alone_does_not_group() -> None:
    messages = [message(1), message(2, seconds=1), message(3, seconds=2)]

    assert find_groups(messages) == []


def test_groups_do_not_overlap() -> None:
    messages = [
        message(1),
        message(2, seconds=3, joined=True),
        message(3, seconds=10, reply_to=1),
    ]

    groups = find_groups(messages)

    assert len(groups) == 1
    assert groups[0].kind == JOINED_RUN


def test_group_size_is_capped() -> None:
    messages = [message(1)] + [message(index, seconds=index, joined=True) for index in range(2, 20)]

    groups = find_groups(messages)

    assert len(groups) == 1
    assert len(groups[0].message_ids) == 10


def test_proposal_id_is_stable() -> None:
    group = find_groups([message(1), message(2, seconds=3, joined=True)])[0]

    assert proposal_id(group) == proposal_id(group)
    assert proposal_id(group).startswith("group-")


def test_render_proposal_lists_members_and_is_not_automatic() -> None:
    messages = [message(1), message(2, seconds=3, joined=True, text="второе")]
    group = find_groups(messages)[0]
    by_id = {item.message_id: item for item in messages}

    text = render_proposal(group, by_id, chat_id=42, layout=LAYOUT)

    assert text.startswith("---\n")
    assert "proposal_id:" in text
    assert "`1`" in text
    assert "`2`" in text
    assert "не выполнено автоматически" in text
    assert "](<../../Telegram/Inbox/" in text
