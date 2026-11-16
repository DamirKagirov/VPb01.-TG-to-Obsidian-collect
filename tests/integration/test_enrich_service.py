"""Обогащение: кэш, бюджет, предложения. Модель подменяется фейком."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import func, select

from kbcollector.config import Settings
from kbcollector.db.engine import build_engine, build_session_factory, create_schema
from kbcollector.db.models import Generation, Note, RawMessage
from kbcollector.domain.ids import kb_id_for, new_uuid
from kbcollector.llm.openai_compat import ChatResult
from kbcollector.llm.service import EnrichmentSetupError, run_enrich
from kbcollector.publication.s3_client import S3Store

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTENT = (
    '{"title": "Настройка n8n", "category": "n8n", "tags": ["n8n", "webhook"], "confidence": 0.9}'
)


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, *, system: str, user: str, max_tokens: int = 400) -> ChatResult:
        self.calls += 1
        assert "n8n" in user
        return ChatResult(content=CONTENT, input_tokens=100, output_tokens=20, model="test-model")


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str | None = None,
        IfNoneMatch: str | None = None,
    ) -> dict[str, str]:
        if IfNoneMatch == "*" and Key in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.objects[Key] = Body
        return {"ETag": '"etag"'}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        if Key not in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "404"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return {"ETag": '"etag"', "ContentLength": len(self.objects[Key])}


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "llm_base_url": "https://llm.test",
        "llm_model": "test-model",
        "llm_api_key": "secret",
        "llm_send_content": True,
        "llm_budget_tokens": 10_000,
        "rubricator_path": PROJECT_ROOT / "config" / "rubricator.yaml",
        "prompt_dir": PROJECT_ROOT / "config" / "prompts",
        "s3_vault_prefix": "vault-test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def seed_notes(settings: Settings, count: int = 2) -> None:
    create_schema(build_engine(settings.db_path))
    factory = build_session_factory(build_engine(settings.db_path))
    with factory() as session:
        for index in range(count):
            message_id = 1000 + index
            session.add(
                RawMessage(
                    id=new_uuid(),
                    source_kind="html_export",
                    chat_id=1,
                    message_id=message_id,
                    sent_at=datetime(2026, 1, 1, index, tzinfo=UTC),
                    author=None,
                    forward_author=None,
                    forward_sent_at=None,
                    reply_to_id=None,
                    is_joined=False,
                    text=f"текст про n8n номер {index}",
                    content_hash=hashlib.sha256(b"x").hexdigest(),
                    payload_json="{}",
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                Note(
                    kb_id=kb_id_for("html_export", 1, message_id),
                    created_at=datetime.now(UTC),
                    published_at=datetime.now(UTC),
                    title=f"старый {index}",
                    category="Inbox",
                    current_path=f"vault-test/Telegram/Inbox/note-{index}.md",
                    state="published",
                    chat_id=1,
                    message_id=message_id,
                    sha256="a" * 64,
                )
            )
        session.commit()


def test_enrich_requests_and_records_usage(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    seed_notes(settings)
    provider = FakeProvider()

    stats = run_enrich(settings, limit=2, provider=provider)  # type: ignore[arg-type]

    assert stats.requested == 2
    assert stats.cached == 0
    assert stats.input_tokens == 200
    assert stats.output_tokens == 40
    assert len(stats.samples) == 2
    assert stats.errors == 0

    factory = build_session_factory(build_engine(settings.db_path))
    with factory() as session:
        generations = session.scalar(select(func.count()).select_from(Generation))
    assert generations == 2


def test_second_run_uses_cache_without_requests(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    seed_notes(settings)
    provider = FakeProvider()

    run_enrich(settings, limit=2, provider=provider)  # type: ignore[arg-type]
    second = run_enrich(settings, limit=2, provider=provider)  # type: ignore[arg-type]

    assert second.cached == 2
    assert second.requested == 0
    assert second.spent_now == 0
    assert provider.calls == 2


def test_budget_stops_requests(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, llm_budget_tokens=100)
    seed_notes(settings, count=3)
    provider = FakeProvider()

    stats = run_enrich(settings, limit=3, provider=provider)  # type: ignore[arg-type]

    assert stats.requested == 1
    assert stats.budget_exhausted is True
    assert provider.calls == 1


def test_proposals_are_published_to_review(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    seed_notes(settings)
    fake = FakeS3Client()
    store = S3Store(fake, "bucket")

    stats = run_enrich(
        settings,
        limit=2,
        propose=True,
        provider=FakeProvider(),
        store=store,  # type: ignore[arg-type]
    )

    assert stats.proposals_created == 2
    assert all("/Review/" in key for key in fake.objects)
    body = next(iter(fake.objects.values())).decode("utf-8")
    assert "Предложение обогащения" in body
    assert "не применено автоматически" in body
    assert "Настройка n8n" in body


def test_dry_run_publishes_nothing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    seed_notes(settings)
    fake = FakeS3Client()

    stats = run_enrich(
        settings,
        limit=2,
        dry_run=True,
        propose=True,
        provider=FakeProvider(),
        store=S3Store(fake, "bucket"),  # type: ignore[arg-type]
    )

    assert stats.proposals_created == 0
    assert fake.objects == {}


def test_send_content_must_be_enabled(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, llm_send_content=False)

    with pytest.raises(EnrichmentSetupError, match="KBC_LLM_SEND_CONTENT"):
        run_enrich(settings, limit=1, provider=FakeProvider())  # type: ignore[arg-type]
