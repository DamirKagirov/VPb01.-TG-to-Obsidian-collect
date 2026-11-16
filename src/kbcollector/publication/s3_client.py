"""Обёртка над S3 с условными записями.

Условные записи подтверждены спайком (ADR 0002): `If-None-Match: *` даёт 412 на
существующем ключе, `If-Match` проверяет ETag. Клиент передаётся снаружи, чтобы
тесты работали без сети.
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from kbcollector.config import Settings

_PRECONDITION_CODES = frozenset({"PreconditionFailed", "412", "ConditionalRequestConflict", "409"})
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class S3Error(RuntimeError):
    """Операция с S3 не удалась."""


class ObjectStat:
    """Метаданные объекта."""

    __slots__ = ("etag", "key", "size")

    def __init__(self, key: str, etag: str, size: int) -> None:
        self.key = key
        self.etag = etag
        self.size = size

    def __repr__(self) -> str:
        return f"ObjectStat(key={self.key!r}, etag={self.etag!r}, size={self.size})"


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _http_status(exc: ClientError) -> str:
    return str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))


class S3Store:
    """Запись и чтение объектов в одном bucket."""

    def __init__(self, client: Any, bucket: str) -> None:  # boto3-клиент не типизирован
        if not bucket:
            msg = "bucket не задан"
            raise S3Error(msg)
        self._client = client
        self.bucket = bucket

    @classmethod
    def from_settings(cls, settings: Settings) -> S3Store:
        if not settings.s3_configured:
            msg = "S3 не настроен: укажите KBC_S3_BUCKET"
            raise S3Error(msg)
        client = build_s3_client(settings)
        return cls(client, settings.s3_bucket or "")

    def put_if_absent(self, key: str, body: bytes, content_type: str) -> ObjectStat | None:
        """Создаёт объект только если его нет. `None` — объект уже существовал."""
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                IfNoneMatch="*",
            )
        except ClientError as exc:
            code = _error_code(exc)
            if code in _PRECONDITION_CODES or _http_status(exc) in _PRECONDITION_CODES:
                return None
            msg = f"не удалось записать {key}: {code or exc}"
            raise S3Error(msg) from exc
        except BotoCoreError as exc:
            msg = f"не удалось записать {key}: {exc}"
            raise S3Error(msg) from exc
        stat = self.head(key)
        if stat is None:
            msg = f"объект {key} записан, но не читается через HEAD"
            raise S3Error(msg)
        return stat

    def put_overwrite(self, key: str, body: bytes, content_type: str) -> ObjectStat:
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=body, ContentType=content_type
            )
        except (ClientError, BotoCoreError) as exc:
            msg = f"не удалось записать {key}: {exc}"
            raise S3Error(msg) from exc
        stat = self.head(key)
        if stat is None:
            msg = f"объект {key} записан, но не читается через HEAD"
            raise S3Error(msg)
        return stat

    def head(self, key: str) -> ObjectStat | None:
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES or _http_status(exc) == "404":
                return None
            msg = f"HEAD {key} не удался: {_error_code(exc) or exc}"
            raise S3Error(msg) from exc
        except BotoCoreError as exc:
            msg = f"HEAD {key} не удался: {exc}"
            raise S3Error(msg) from exc
        return ObjectStat(
            key=key,
            etag=str(response.get("ETag", "")).strip('"'),
            size=int(response.get("ContentLength", 0)),
        )

    def get_bytes(self, key: str) -> bytes | None:
        """Содержимое объекта или `None`, если объекта нет."""
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES or _http_status(exc) == "404":
                return None
            msg = f"GET {key} не удался: {_error_code(exc) or exc}"
            raise S3Error(msg) from exc
        except BotoCoreError as exc:
            msg = f"GET {key} не удался: {exc}"
            raise S3Error(msg) from exc
        data = response["Body"].read()
        return bytes(data)

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            msg = f"не удалось удалить {key}: {exc}"
            raise S3Error(msg) from exc


def build_s3_client(settings: Settings) -> Any:  # boto3-клиент
    session = boto3.session.Session(
        aws_access_key_id=(
            settings.s3_access_key_id.get_secret_value() if settings.s3_access_key_id else None
        ),
        aws_secret_access_key=(
            settings.s3_secret_access_key.get_secret_value()
            if settings.s3_secret_access_key
            else None
        ),
        region_name=settings.s3_region,
    )
    return session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 10, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
        ),
    )
