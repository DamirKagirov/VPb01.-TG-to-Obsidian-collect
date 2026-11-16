"""Обогащение существующих заметок: кэш → модель → валидация → предложения.

Опубликованные заметки не изменяются: результат сохраняется как предложение
в `Review/` (ТЗ §4.4). Кэш и учёт токенов ведутся в таблице `generations`,
поэтому повторный запуск с теми же промптом/моделью/входом не тратит бюджет.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kbcollector.classification.rubricator import (
    Rubricator,
    RubricatorError,
    default_rubricator,
    load_rubricator,
)
from kbcollector.config import Settings
from kbcollector.db.engine import build_engine, build_session_factory
from kbcollector.db.models import Generation, Note, RawMessage
from kbcollector.domain.ids import new_uuid
from kbcollector.domain.message import SOURCE_HTML_EXPORT
from kbcollector.llm.openai_compat import (
    LLMError,
    OpenAICompatibleProvider,
    parse_json_object,
)
from kbcollector.llm.prompts import PromptError, PromptTemplate, load_prompt, render_user
from kbcollector.llm.proposal import (
    enrichment_proposal_key,
    render_enrichment_proposal,
)
from kbcollector.llm.schema import (
    Enrichment,
    EnrichmentError,
    as_json,
    parse_enrichment,
)
from kbcollector.logging import get_logger
from kbcollector.publication.layout import VaultLayout
from kbcollector.publication.s3_client import S3Error, S3Store

logger = get_logger(__name__)

DEFAULT_LIMIT = 50
_SAMPLE_COUNT = 3


class EnrichmentSetupError(RuntimeError):
    """Обогащение невозможно из-за конфигурации."""


@dataclass(slots=True)
class EnrichmentStats:
    dry_run: bool
    propose: bool
    limit: int
    budget: int
    spent_before: int = 0
    processed: int = 0
    cached: int = 0
    requested: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    errors: int = 0
    skipped_empty: int = 0
    budget_exhausted: bool = False
    proposals_created: int = 0
    proposals_existing: int = 0
    samples: list[dict[str, object]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def spent_now(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class _Row:
    chat_id: int
    message_id: int
    text: str
    sent_at: datetime
    forward_author: str | None
    note_key: str | None
    note_title: str
    note_sha256: str | None


def _load_rubricator(settings: Settings) -> Rubricator:
    path = settings.rubricator_path
    if not path.is_file():
        logger.warning("rubricator_missing", path=str(path))
        return default_rubricator()
    try:
        return load_rubricator(path)
    except RubricatorError as exc:
        raise EnrichmentSetupError(str(exc)) from exc


def _load_prompt(settings: Settings) -> PromptTemplate:
    filename = f"enrich.{settings.llm_prompt_version}.md"
    path = settings.prompt_dir / filename
    try:
        return load_prompt(path, version=settings.llm_prompt_version)
    except PromptError as exc:
        raise EnrichmentSetupError(str(exc)) from exc


def _input_hash(
    *,
    prompt: PromptTemplate,
    model: str,
    rubricator: Rubricator,
    text: str,
) -> str:
    payload = "\x1f".join(
        [
            prompt.version,
            model,
            "|".join(rubricator.categories),
            prompt.system,
            prompt.user_template,
            text,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _select_rows(session: Session, *, limit: int) -> list[_Row]:
    statement = (
        select(
            RawMessage.chat_id,
            RawMessage.message_id,
            RawMessage.text,
            RawMessage.sent_at,
            RawMessage.forward_author,
            Note.current_path,
            Note.title,
            Note.sha256,
        )
        .join(
            Note,
            (Note.chat_id == RawMessage.chat_id) & (Note.message_id == RawMessage.message_id),
        )
        .where(Note.state == "published")
        .order_by(RawMessage.message_id)
        .limit(limit)
    )
    return [
        _Row(
            chat_id=int(row[0]),
            message_id=int(row[1]),
            text=str(row[2]),
            sent_at=row[3],
            forward_author=row[4],
            note_key=row[5],
            note_title=str(row[6]),
            note_sha256=row[7],
        )
        for row in session.execute(statement)
    ]


def _tokens_spent(session: Session) -> int:
    total = session.scalar(
        select(
            func.coalesce(func.sum(Generation.input_tokens + Generation.output_tokens), 0)
        ).where(Generation.status == "ok")
    )
    return int(total or 0)


def _store_generation(
    session: Session,
    *,
    row: _Row,
    prompt: PromptTemplate,
    model: str,
    prompt_hash: str,
    enrichment: Enrichment,
    input_tokens: int,
    output_tokens: int,
) -> None:
    session.add(
        Generation(
            id=new_uuid(),
            source_kind=SOURCE_HTML_EXPORT,
            chat_id=row.chat_id,
            message_id=row.message_id,
            prompt_version=prompt.version,
            model=model,
            params_json=json.dumps({"temperature": 0.0}, ensure_ascii=False),
            input_hash=prompt_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=None,
            output_json=as_json(enrichment),
            status="ok",
            error=None,
            created_at=datetime.now(UTC),
        )
    )


def run_enrich(
    settings: Settings,
    *,
    limit: int = DEFAULT_LIMIT,
    dry_run: bool = False,
    propose: bool = False,
    budget_tokens: int | None = None,
    provider: OpenAICompatibleProvider | None = None,
    store: S3Store | None = None,
) -> EnrichmentStats:
    """Обогащает до `limit` существующих заметок. Заметки не меняются."""
    if not settings.llm_configured:
        msg = "LLM не настроен: нужны KBC_LLM_BASE_URL, KBC_LLM_MODEL, KBC_LLM_API_KEY"
        raise EnrichmentSetupError(msg)
    if not settings.llm_send_content:
        msg = (
            "отправка текста внешней модели выключена: задайте KBC_LLM_SEND_CONTENT=true "
            "(текст сообщений уйдёт провайдеру)"
        )
        raise EnrichmentSetupError(msg)

    rubricator = _load_rubricator(settings)
    prompt = _load_prompt(settings)
    model = settings.llm_model or ""
    budget = budget_tokens if budget_tokens is not None else settings.llm_budget_tokens

    session_factory = build_session_factory(build_engine(settings.db_path))
    stats = EnrichmentStats(dry_run=dry_run, propose=propose, limit=limit, budget=budget)

    active_provider = provider or OpenAICompatibleProvider.from_settings(settings)
    vault = VaultLayout(settings.s3_vault_prefix)
    active_store: S3Store | None = store
    if propose and not dry_run and active_store is None:
        active_store = S3Store.from_settings(settings)

    with session_factory() as session:
        stats.spent_before = _tokens_spent(session)
        rows = _select_rows(session, limit=limit)

        for row in rows:
            stats.processed += 1
            text = row.text.strip()
            if not text:
                # Медиа без подписи: классифицировать нечего, это не ошибка.
                stats.skipped_empty += 1
                continue

            if len(text) > settings.llm_max_input_chars:
                text = text[: settings.llm_max_input_chars].rstrip() + "\n…[обрезано]"

            prompt_hash = _input_hash(prompt=prompt, model=model, rubricator=rubricator, text=text)
            enrichment = _from_cache(session, prompt_hash, rubricator)
            if enrichment is not None:
                stats.cached += 1
            else:
                used = stats.spent_before + stats.spent_now
                if used >= budget:
                    stats.budget_exhausted = True
                    break
                enrichment = _request(
                    provider=active_provider,
                    prompt=prompt,
                    rubricator=rubricator,
                    row=row,
                    text=text,
                    stats=stats,
                    model=model,
                    prompt_hash=prompt_hash,
                    session=session,
                )
                if enrichment is None:
                    continue

            if len(stats.samples) < _SAMPLE_COUNT:
                stats.samples.append(
                    {
                        "message_id": row.message_id,
                        "title": enrichment.title,
                        "category": enrichment.category,
                        "tags": list(enrichment.tags),
                        "confidence": enrichment.confidence,
                    }
                )

            if propose and not dry_run and active_store is not None:
                _publish_proposal(
                    store=active_store,
                    vault=vault,
                    row=row,
                    enrichment=enrichment,
                    prompt_hash=prompt_hash,
                    stats=stats,
                )

        session.commit()

    return stats


def _from_cache(session: Session, prompt_hash: str, rubricator: Rubricator) -> Enrichment | None:
    cached = session.scalar(
        select(Generation).where(Generation.input_hash == prompt_hash, Generation.status == "ok")
    )
    if cached is None:
        return None
    try:
        return parse_enrichment(cached.output_json, rubricator)
    except EnrichmentError:
        return None


def _request(
    *,
    provider: OpenAICompatibleProvider,
    prompt: PromptTemplate,
    rubricator: Rubricator,
    row: _Row,
    text: str,
    stats: EnrichmentStats,
    model: str,
    prompt_hash: str,
    session: Session,
) -> Enrichment | None:
    user = render_user(
        prompt,
        categories=rubricator.categories,
        sent_at=row.sent_at,
        forward_author=row.forward_author,
        text=text,
    )
    try:
        result = provider.complete_json(system=prompt.system, user=user)
        raw_json = parse_json_object(result.content)
        enrichment = parse_enrichment(raw_json, rubricator)
    except (LLMError, EnrichmentError) as exc:
        stats.errors += 1
        if len(stats.failures) < 20:
            stats.failures.append(f"{row.message_id}: {exc}")
        logger.warning("enrich_failed", message_id=row.message_id, error=str(exc))
        return None

    stats.requested += 1
    stats.input_tokens += result.input_tokens
    stats.output_tokens += result.output_tokens
    _store_generation(
        session,
        row=row,
        prompt=prompt,
        model=model,
        prompt_hash=prompt_hash,
        enrichment=enrichment,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    return enrichment


def _publish_proposal(
    *,
    store: S3Store,
    vault: VaultLayout,
    row: _Row,
    enrichment: Enrichment,
    prompt_hash: str,
    stats: EnrichmentStats,
) -> None:
    key = enrichment_proposal_key(
        chat_id=row.chat_id,
        message_id=row.message_id,
        input_hash=prompt_hash,
        layout=vault,
    )
    body = render_enrichment_proposal(
        enrichment,
        input_hash=prompt_hash,
        note_key=row.note_key or "",
        base_sha256=row.note_sha256,
        current_title=row.note_title,
    )
    try:
        created = store.put_if_absent(key, body.encode("utf-8"), "text/markdown; charset=utf-8")
    except S3Error as exc:
        stats.errors += 1
        if len(stats.failures) < 20:
            stats.failures.append(f"{row.message_id}: {exc}")
        return
    if created is None:
        stats.proposals_existing += 1
    else:
        stats.proposals_created += 1


def prompt_path(settings: Settings) -> Path:
    return settings.prompt_dir / f"enrich.{settings.llm_prompt_version}.md"
