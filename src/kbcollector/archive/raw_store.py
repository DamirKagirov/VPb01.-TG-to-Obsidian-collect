"""Неизменяемый архив исходных данных.

Требования (ТЗ §10.4): исходный экспорт не изменяется, копия проверяется по хешу,
все версии сохраняются. Архив живёт вне синхронизируемого vault-префикса.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from kbcollector.domain.ids import new_source_id

_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArchivedFile:
    name: str
    size: int
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_export(pages: tuple[Path, ...], archive_root: Path) -> tuple[str, Path]:
    """Копирует страницы экспорта в архив и возвращает `(source_id, каталог)`.

    Копия создаётся рядом с манифестом, исходные файлы не изменяются.
    """
    source_id = new_source_id()
    target = archive_root / "sources" / source_id
    target.mkdir(parents=True, exist_ok=True)

    files: list[ArchivedFile] = []
    for page in pages:
        destination = target / page.name
        shutil.copyfile(page, destination)
        original_hash = sha256_file(page)
        copied_hash = sha256_file(destination)
        if original_hash != copied_hash:
            msg = f"копия {page.name} не совпадает с оригиналом по sha256"
            raise OSError(msg)
        files.append(
            ArchivedFile(name=page.name, size=destination.stat().st_size, sha256=copied_hash)
        )

    manifest = {
        "source_id": source_id,
        "created_at": datetime.now(UTC).isoformat(),
        "files": [asdict(item) for item in files],
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return source_id, target
