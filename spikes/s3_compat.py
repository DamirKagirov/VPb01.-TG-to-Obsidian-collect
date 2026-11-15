"""Спайк S1/S2: совместимость S3 и поддержка условных записей.

Проверяет только S3-сторону сценария «Python → S3 → Obsidian»:
создание/чтение/перечисление объектов, кириллицу и emoji в ключах,
вложенные пути, переименование (copy+delete), удаление, версионирование
и — главное — поддержку `If-None-Match` / `If-Match` (ТЗ §14.4).

Работает в изолированном префиксе `_spike/<uuid>/` вне vault-префикса
и удаляет за собой все созданные объекты.

Запуск:
    uv run python spikes/s3_compat.py
    uv run python spikes/s3_compat.py --keep       # не удалять объекты
    uv run python spikes/s3_compat.py --json out.json
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from kbcollector.config import Settings

PASSED = "passed"
FAILED = "failed"
UNSUPPORTED = "unsupported"

SPIKE_ROOT = "_spike"


@dataclass(slots=True)
class Result:
    check: str
    status: str
    detail: str


def _md5_hex(body: bytes) -> str:
    return hashlib.md5(body).hexdigest()  # ETag-совместимость, не криптография


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "Unknown"))


def build_client(settings: Settings) -> Any:  # boto3-клиент не типизирован в source
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
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=30,
        ),
    )


def _record(results: list[Result], check: str, status: str, detail: str) -> None:
    results.append(Result(check, status, detail))


def run(settings: Settings, *, keep: bool = False) -> list[Result]:
    results: list[Result] = []
    if not settings.s3_configured:
        _record(results, "config", FAILED, "KBC_S3_BUCKET не задан")
        return results

    bucket = settings.s3_bucket or ""
    client = build_client(settings)
    run_prefix = f"{SPIKE_ROOT}/{uuid.uuid4().hex}/"
    created: list[str] = []

    try:
        client.head_bucket(Bucket=bucket)
        _record(results, "bucket_access", PASSED, f"bucket={bucket} доступен")
    except (ClientError, BotoCoreError) as exc:
        code = _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__
        _record(results, "bucket_access", FAILED, f"нет доступа к bucket: {code}")
        return results

    # 1. Запись Markdown с кириллицей, пробелами и emoji в ключе.
    key = f"{run_prefix}Тест/Кириллица и emoji 🚀.md"
    body = (
        "---\n"
        'kb_id: "note_spike"\n'
        "title: Кириллица и emoji 🚀\n"
        "---\n\n"
        "# Проверка совместимости\n\n"
        "Текст с кириллицей, `inline code` и ссылкой [example](https://example.org).\n"
    ).encode()

    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="text/markdown; charset=utf-8",
        )
        created.append(key)
        _record(results, "put_unicode_key", PASSED, f"создан объект {key!r}")
    except (ClientError, BotoCoreError) as exc:
        _record(results, "put_unicode_key", FAILED, str(exc))
        return results

    # 2. Чтение и побайтовое совпадение.
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        downloaded = response["Body"].read()
        if downloaded == body:
            _record(results, "get_roundtrip", PASSED, "содержимое совпадает побайтово")
        else:
            _record(results, "get_roundtrip", FAILED, "содержимое изменилось при передаче")
    except (ClientError, BotoCoreError) as exc:
        _record(results, "get_roundtrip", FAILED, str(exc))

    # 3. ETag: для одночастной загрузки должен совпадать с MD5.
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        etag = str(head["ETag"]).strip('"')
        expected = _md5_hex(body)
        if etag == expected:
            _record(results, "etag_is_md5", PASSED, f"ETag = MD5 ({etag[:8]}…)")
        else:
            _record(
                results,
                "etag_is_md5",
                UNSUPPORTED,
                f"ETag ≠ MD5: {etag[:12]}… (использовать только как подсказку)",
            )
    except (ClientError, BotoCoreError) as exc:
        _record(results, "etag_is_md5", FAILED, str(exc))

    # 4. Перечисление с разделителем — так сверка увидит «папки».
    try:
        listing = client.list_objects_v2(Bucket=bucket, Prefix=run_prefix, Delimiter="/")
        prefixes = [item["Prefix"] for item in listing.get("CommonPrefixes", [])]
        if any("Тест/" in prefix for prefix in prefixes):
            _record(results, "list_delimiter_unicode", PASSED, f"CommonPrefixes={prefixes}")
        else:
            _record(results, "list_delimiter_unicode", FAILED, f"префикс не найден: {prefixes}")
    except (ClientError, BotoCoreError) as exc:
        _record(results, "list_delimiter_unicode", FAILED, str(exc))

    # 5. Условное создание: If-None-Match="*" на существующем ключе должно дать 412.
    try:
        client.put_object(Bucket=bucket, Key=key, Body=b"overwrite", IfNoneMatch="*")
        _record(
            results,
            "if_none_match_supported",
            UNSUPPORTED,
            "провайдер проигнорировал If-None-Match: объект перезаписан",
        )
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412:
            _record(results, "if_none_match_supported", PASSED, "412 PreconditionFailed")
        else:
            _record(
                results,
                "if_none_match_supported",
                UNSUPPORTED,
                f"ответ {_error_code(exc)} вместо 412",
            )
    except BotoCoreError as exc:
        _record(results, "if_none_match_supported", FAILED, str(exc))

    # 6. Условная замена: If-Match с актуальным ETag.
    current_etag: str | None = None
    with contextlib.suppress(ClientError, BotoCoreError):
        current_etag = str(client.head_object(Bucket=bucket, Key=key)["ETag"])

    updated = body + b"\n<!-- updated -->\n"
    if current_etag:
        try:
            client.put_object(Bucket=bucket, Key=key, Body=updated, IfMatch=current_etag)
            body = updated
            _record(results, "if_match_supported", PASSED, "условная замена с актуальным ETag")
        except ClientError as exc:
            _record(
                results,
                "if_match_supported",
                UNSUPPORTED,
                f"{_error_code(exc)}: условная замена недоступна",
            )
        except BotoCoreError as exc:
            _record(results, "if_match_supported", FAILED, str(exc))

        # 7. Условная замена с устаревшим ETag должна быть отклонена.
        stale_etag = '"' + "0" * 32 + '"'
        try:
            client.put_object(Bucket=bucket, Key=key, Body=b"stale", IfMatch=stale_etag)
            _record(
                results,
                "if_match_rejects_stale",
                UNSUPPORTED,
                "устаревший If-Match принят — условная защита не работает",
            )
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412:
                _record(results, "if_match_rejects_stale", PASSED, "412 PreconditionFailed")
            else:
                _record(
                    results,
                    "if_match_rejects_stale",
                    UNSUPPORTED,
                    f"ответ {_error_code(exc)} вместо 412",
                )
        except BotoCoreError as exc:
            _record(results, "if_match_rejects_stale", FAILED, str(exc))

    # 8. Переименование = copy + delete (так его увидит Obsidian).
    renamed_key = f"{run_prefix}Тест/Переименовано 🚀.md"
    try:
        client.copy_object(
            Bucket=bucket,
            Key=renamed_key,
            CopySource={"Bucket": bucket, "Key": key},
            MetadataDirective="COPY",
        )
        created.append(renamed_key)
        client.delete_object(Bucket=bucket, Key=key)
        created.remove(key)
        head = client.head_object(Bucket=bucket, Key=renamed_key)
        if int(head["ContentLength"]) == len(body):
            _record(results, "rename_copy_delete", PASSED, "copy+delete сохраняет содержимое")
        else:
            _record(results, "rename_copy_delete", FAILED, "размер после copy+delete отличается")
    except (ClientError, BotoCoreError) as exc:
        _record(results, "rename_copy_delete", FAILED, str(exc))

    # 9. Версионирование бакета — влияет на политику замен и восстановления.
    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
        status = versioning.get("Status", "NotEnabled")
        _record(results, "bucket_versioning", PASSED, f"Status={status}")
    except (ClientError, BotoCoreError) as exc:
        _record(results, "bucket_versioning", UNSUPPORTED, str(exc))

    # Очистка: удаляем только объекты этого запуска.
    try:
        listing = client.list_objects_v2(Bucket=bucket, Prefix=run_prefix)
        keys = [item["Key"] for item in listing.get("Contents", [])]
        if keys and not keep:
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": item} for item in keys], "Quiet": True},
            )
            _record(results, "cleanup", PASSED, f"удалено объектов: {len(keys)}")
        elif keep:
            _record(results, "cleanup", PASSED, f"оставлено для проверки из Obsidian: {keys}")
        else:
            _record(results, "cleanup", PASSED, "нечего удалять")
    except (ClientError, BotoCoreError) as exc:
        _record(results, "cleanup", FAILED, str(exc))

    return results


def _print_report(results: list[Result], settings: Settings, *, json_path: str | None) -> None:
    width = max((len(item.check) for item in results), default=10)
    print(f"S3: {settings.s3_endpoint_url or 'AWS'} / bucket={settings.s3_bucket}")
    for item in results:
        marker = {PASSED: "OK  ", FAILED: "FAIL", UNSUPPORTED: "N/A "}[item.status]
        print(f"[{marker}] {item.check.ljust(width)}  {item.detail}")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "endpoint": settings.s3_endpoint_url,
        "bucket": settings.s3_bucket,
        "vault_prefix": settings.s3_vault_prefix,
        "results": [asdict(item) for item in results],
    }
    if json_path:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"\nJSON: {json_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="не удалять тестовые объекты")
    parser.add_argument("--json", dest="json_path", default=None, help="сохранить отчёт в JSON")
    args = parser.parse_args()

    settings = Settings()
    results = run(settings, keep=bool(args.keep))
    _print_report(results, settings, json_path=args.json_path)

    failures = [item for item in results if item.status == FAILED]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
