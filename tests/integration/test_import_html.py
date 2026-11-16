"""Сквозной импорт: экспорт → БД → S3 (фейковый клиент), идемпотентность."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import func, select

from kbcollector.config import Settings
from kbcollector.db.engine import build_engine, build_session_factory, create_schema
from kbcollector.db.models import Note
from kbcollector.publication.s3_client import S3Store
from kbcollector.services.import_html import run_import

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "html"

pytestmark = pytest.mark.integration


class FakeS3Client:
    """Минимальная реализация условных записей для проверки контракта."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

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
                    "Error": {"Code": "PreconditionFailed", "Message": "exists"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.objects[Key] = Body
        self.content_types[Key] = ContentType or ""
        return {"ETag": f'"{hashlib.md5(Body).hexdigest()}"'}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        if Key not in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "not found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        body = self.objects[Key]
        return {
            "ETag": f'"{hashlib.md5(body).hexdigest()}"',
            "ContentLength": len(body),
        }


def prepare_export(tmp_path: Path, fixture: str = "sample.html") -> Path:
    root = tmp_path / "export"
    root.mkdir()
    shutil.copyfile(FIXTURES / fixture, root / "messages.html")
    return root


def make_settings(tmp_path: Path, export_root: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        data_dir=tmp_path / "data",
        html_export_dir=export_root,
        html_chat_id=42,
        s3_bucket="test-bucket",
        s3_vault_prefix="vault-test",
    )


def test_import_creates_notes_and_objects(tmp_path: Path) -> None:
    export_root = prepare_export(tmp_path)
    settings = make_settings(tmp_path, export_root)
    create_schema(build_engine(settings.db_path))
    fake = FakeS3Client()

    stats = run_import(settings, store=S3Store(fake, "test-bucket"))

    assert stats.parsed == 5
    assert stats.notes_created == 5
    assert stats.notes_skipped == 0
    assert len(fake.objects) == 5
    assert all(key.startswith("vault-test/Telegram/Inbox/") for key in fake.objects)

    sample_key = stats.imported_keys[0]
    note = fake.objects[sample_key].decode("utf-8")
    assert note.startswith("---\n")
    assert 'kb_id: "note_' in note
    assert "## Источники" in note

    factory = build_session_factory(build_engine(settings.db_path))
    with factory() as session:
        count = session.scalar(select(func.count()).select_from(Note))
    assert count == 5


def test_second_import_is_idempotent(tmp_path: Path) -> None:
    export_root = prepare_export(tmp_path)
    settings = make_settings(tmp_path, export_root)
    create_schema(build_engine(settings.db_path))
    fake = FakeS3Client()
    store = S3Store(fake, "test-bucket")

    first = run_import(settings, store=store)
    keys_after_first = set(fake.objects)
    second = run_import(settings, store=store)

    assert first.notes_created == 5
    assert second.notes_created == 0
    assert second.notes_skipped == 5
    assert set(fake.objects) == keys_after_first
    assert second.attachments_uploaded == 0


def test_media_attachments_and_placeholders(tmp_path: Path) -> None:
    export_root = prepare_export(tmp_path, "media.html")
    (export_root / "photos").mkdir()
    (export_root / "photos" / "photo_1@01-01-2026_11-00-00.jpg").write_bytes(b"jpeg-bytes")
    (export_root / "files").mkdir()
    (export_root / "files" / "Документ тестовый.docx").write_bytes(b"docx-bytes")

    settings = make_settings(tmp_path, export_root)
    create_schema(build_engine(settings.db_path))
    fake = FakeS3Client()

    stats = run_import(settings, store=S3Store(fake, "test-bucket"))

    assert stats.notes_created == 3
    assert stats.attachments_uploaded == 2
    assert stats.attachments_not_included == 1

    notes = {key: body.decode("utf-8") for key, body in fake.objects.items() if key.endswith(".md")}
    photo_note = next(text for text in notes.values() if "Подпись к фото" in text)
    assert "../../Attachments/Telegram/" in photo_note
    assert "photo_1@01-01-2026_11-00-00.jpg" in photo_note

    placeholder_note = next(text for text in notes.values() if "Медиа не включено" in text)
    assert "файл не включён в экспорт Telegram" in placeholder_note


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    export_root = prepare_export(tmp_path)
    settings = make_settings(tmp_path, export_root)
    fake = FakeS3Client()

    stats = run_import(settings, dry_run=True, store=S3Store(fake, "test-bucket"))

    assert stats.parsed == 5
    assert stats.notes_created == 0
    assert fake.objects == {}
    assert not settings.db_path.exists()
    assert stats.sample_path is not None
    assert stats.sample_path.exists()


def test_limit_restricts_processed_messages(tmp_path: Path) -> None:
    export_root = prepare_export(tmp_path)
    settings = make_settings(tmp_path, export_root)
    create_schema(build_engine(settings.db_path))
    fake = FakeS3Client()

    stats = run_import(settings, limit=2, store=S3Store(fake, "test-bucket"))

    assert stats.parsed == 2
    assert stats.notes_created == 2
    assert len(fake.objects) == 2


def test_missing_chat_id_is_configuration_error(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")  # type: ignore[call-arg]

    with pytest.raises(Exception, match="KBC_HTML_CHAT_ID"):
        run_import(settings, dry_run=True)
