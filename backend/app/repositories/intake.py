"""AI intake, user side: runs and scope items under the caller's RLS session.

The background worker's writes live in `app.repositories.intake_jobs`
(service connection), mirroring `app.repositories.optimization`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.intake import PASTED_TITLE
from app.core.errors import pg_error_info
from app.domain.common import (
    DocumentKind,
    Evidence,
    ItemOrigin,
    JobStatus,
    ScopeCategory,
    ScopeStatus,
)
from app.domain.intake import IntakeRun, ScopeItem
from app.repositories.tables import intake_runs, projects, scope_items, source_documents

ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)
RUN_HISTORY_LIMIT = 20
run_t = intake_runs
item_t = scope_items
doc_t = source_documents

_ITEM_COLUMNS = (
    item_t.c.id,
    item_t.c.project_id,
    item_t.c.category,
    item_t.c.text,
    item_t.c.status,
    item_t.c.origin,
    item_t.c.confidence,
    item_t.c.source_quote,
    item_t.c.evidence,
    item_t.c.evidence_note,
    item_t.c.suggested_text,
    item_t.c.source_document_id,
    item_t.c.sort_order,
    item_t.c.created_at,
    item_t.c.updated_at,
)


class ActiveRunExistsError(Exception):
    """The one-active-run-per-project unique index rejected the insert."""


@dataclass(frozen=True, slots=True)
class ActiveRun:
    id: uuid.UUID
    stale: bool


def readable_uploads(project_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """Uploaded documents of the project whose text was read."""
    return sa.and_(
        doc_t.c.project_id == project_id,
        doc_t.c.kind != DocumentKind.TEXT,
        doc_t.c.extraction_status == JobStatus.SUCCEEDED,
        doc_t.c.content_text.is_not(None),
    )


def _run_select() -> sa.Select[Any]:
    return sa.select(
        run_t.c.id,
        run_t.c.project_id,
        run_t.c.status,
        run_t.c.model,
        run_t.c.items_extracted,
        run_t.c.error,
        run_t.c.created_at,
        run_t.c.started_at,
        run_t.c.finished_at,
    )


def _to_run(row: sa.Row[Any]) -> IntakeRun:
    return IntakeRun.model_validate(dict(row._mapping))


def _to_item(row: sa.Row[Any]) -> ScopeItem:
    return ScopeItem.model_validate(dict(row._mapping))


def older_than(seconds: int) -> sa.ColumnElement[bool]:
    """Active runs started (or queued) more than `seconds` ago: orphaned by a restart."""
    cutoff = sa.func.now() - sa.func.make_interval(0, 0, 0, 0, 0, 0, seconds)
    return sa.func.coalesce(run_t.c.started_at, run_t.c.created_at) < cutoff


class IntakeRepository:
    """User-facing queries: everything runs under the caller's RLS session."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        stmt = sa.select(projects.c.id).where(projects.c.id == project_id)
        return (await self._conn.execute(stmt)).first() is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        stmt = sa.select(sa.func.private.can_write_project(project_id))
        return bool((await self._conn.execute(stmt)).scalar())

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        stmt = (
            sa.select(run_t.c.id, older_than(stale_after_s).label("stale"))
            .where(run_t.c.project_id == project_id, run_t.c.status.in_(ACTIVE))
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return ActiveRun(id=row.id, stale=bool(row.stale)) if row else None

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        await self._conn.execute(
            sa.update(run_t)
            .where(run_t.c.id == run_id, run_t.c.status.in_(ACTIVE))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def has_readable_upload(self, project_id: uuid.UUID) -> bool:
        """Whether an upload's text was read, so a run can start without pasted notes."""
        stmt = sa.select(doc_t.c.id).where(readable_uploads(project_id)).limit(1)
        return (await self._conn.execute(stmt)).first() is not None

    async def create(self, project_id: uuid.UUID, text: str) -> uuid.UUID:
        """Queue a run; non-empty pasted notes are stored as its 'text' document.

        Both rows are written in this transaction, so they share `created_at`
        (now() is the transaction start time): that is how the worker finds the
        run's own notes without a link column.
        """
        run_id = uuid.uuid4()
        try:
            await self._conn.execute(
                sa.insert(run_t).values(id=run_id, project_id=project_id, status=JobStatus.QUEUED)
            )
        except IntegrityError as exc:
            if pg_error_info(exc).sqlstate == "23505":
                raise ActiveRunExistsError from exc
            raise
        if text:
            await self._conn.execute(
                sa.insert(doc_t).values(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    kind=DocumentKind.TEXT,
                    title=PASTED_TITLE,
                    content_text=text,
                    extraction_status=JobStatus.SUCCEEDED,
                )
            )
        return run_id

    async def get(self, project_id: uuid.UUID, run_id: uuid.UUID) -> IntakeRun | None:
        stmt = _run_select().where(run_t.c.project_id == project_id, run_t.c.id == run_id)
        row = (await self._conn.execute(stmt)).first()
        return _to_run(row) if row else None

    async def list_runs(self, project_id: uuid.UUID) -> list[IntakeRun]:
        """The project's most recent runs, newest first ("Previous Intakes")."""
        stmt = (
            _run_select()
            .where(run_t.c.project_id == project_id)
            .order_by(run_t.c.created_at.desc(), run_t.c.id.desc())
            .limit(RUN_HISTORY_LIMIT)
        )
        return [_to_run(row) for row in (await self._conn.execute(stmt)).all()]

    async def latest(self, project_id: uuid.UUID) -> IntakeRun | None:
        stmt = (
            _run_select()
            .where(run_t.c.project_id == project_id)
            .order_by(run_t.c.created_at.desc(), run_t.c.id.desc())
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return _to_run(row) if row else None

    async def list_scope_items(
        self, project_id: uuid.UUID, *, include_removed: bool = False
    ) -> list[ScopeItem]:
        """The project's scope items by category and order; removed ones only when asked
        (Scope Review lists them under "Removed" with Undo)."""
        stmt = (
            sa.select(*_ITEM_COLUMNS)
            .where(item_t.c.project_id == project_id)
            .order_by(item_t.c.category, item_t.c.sort_order)
        )
        if not include_removed:
            stmt = stmt.where(item_t.c.status != ScopeStatus.REMOVED)
        return [_to_item(row) for row in (await self._conn.execute(stmt)).all()]

    async def create_item(
        self, project_id: uuid.UUID, category: ScopeCategory, text: str
    ) -> ScopeItem | None:
        """Add a manual, accepted item at the end of its category.

        Evidence stays null: the item is the user's own statement ("Yours").
        """
        item_id = uuid.uuid4()
        next_order = (
            sa.select(sa.func.coalesce(sa.func.max(item_t.c.sort_order) + 1, 0))
            .where(item_t.c.project_id == project_id, item_t.c.category == category)
            .scalar_subquery()
        )
        await self._conn.execute(
            sa.insert(item_t).values(
                id=item_id,
                project_id=project_id,
                category=category,
                text=text,
                status=ScopeStatus.ACCEPTED,
                origin=ItemOrigin.MANUAL,
                sort_order=next_order,
            )
        )
        stmt = sa.select(*_ITEM_COLUMNS).where(item_t.c.id == item_id)
        row = (await self._conn.execute(stmt)).first()
        return _to_item(row) if row else None

    async def update_item(self, item_id: uuid.UUID, patch: dict[str, Any]) -> ScopeItem | None:
        """Apply a partial update (text / status); None when the item is not visible.

        Setting the text to exactly the item's `suggested_text` ("Use the quoted
        version" of a flagged item) makes the item 'stated' and clears the note
        and the suggestion. In SQL every CASE reads the row's old values.
        """
        if patch:
            values: dict[str, Any] = {**patch, "updated_at": sa.func.now()}
            new_text = patch.get("text")
            if new_text is not None:
                takes_suggestion = item_t.c.suggested_text == new_text
                values["evidence"] = sa.case(
                    (takes_suggestion, sa.literal(Evidence.STATED.value)), else_=item_t.c.evidence
                )
                values["evidence_note"] = sa.case(
                    (takes_suggestion, sa.null()), else_=item_t.c.evidence_note
                )
                values["suggested_text"] = sa.case(
                    (takes_suggestion, sa.null()), else_=item_t.c.suggested_text
                )
            await self._conn.execute(
                sa.update(item_t).where(item_t.c.id == item_id).values(**values)
            )
        stmt = sa.select(*_ITEM_COLUMNS).where(item_t.c.id == item_id)
        row = (await self._conn.execute(stmt)).first()
        return _to_item(row) if row else None
