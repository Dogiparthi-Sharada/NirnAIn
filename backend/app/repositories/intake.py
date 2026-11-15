"""AI intake, user side: intake runs and scope items under the caller's RLS session.

Purpose: Queue one intake run at a time per project, store the pasted notes with it and let the PM
review, edit, add and remove scope items, with the evidence rules ("never invent") applied when the
PM takes the quoted version of a flagged item.
How it fits: Used by app/api/v1/intake.py; the background worker's writes live in
app/repositories/intake_jobs.py (service connection); layer: repositories.
Created: 2026-10-21 · Version: 1.0 (NirnAIn demo release)
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
from app.repositories import access
from app.repositories.job_rows import started_before
from app.repositories.tables import intake_runs, projects, scope_items, source_documents

ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)
RUN_HISTORY_LIMIT = 20

_ITEM_COLUMNS = (
    scope_items.c.id,
    scope_items.c.project_id,
    scope_items.c.category,
    scope_items.c.text,
    scope_items.c.status,
    scope_items.c.origin,
    scope_items.c.confidence,
    scope_items.c.source_quote,
    scope_items.c.evidence,
    scope_items.c.evidence_note,
    scope_items.c.suggested_text,
    scope_items.c.source_document_id,
    scope_items.c.sort_order,
    scope_items.c.created_at,
    scope_items.c.updated_at,
)


class ActiveRunExistsError(Exception):
    """The one-active-run-per-project unique index rejected the insert."""


@dataclass(frozen=True, slots=True)
class ActiveRun:
    """A queued or running intake run and whether it has been active for too long (orphaned)."""

    id: uuid.UUID
    stale: bool


def readable_uploads(project_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """Uploaded documents of the project whose text was read."""
    return sa.and_(
        source_documents.c.project_id == project_id,
        source_documents.c.kind != DocumentKind.TEXT,
        source_documents.c.extraction_status == JobStatus.SUCCEEDED,
        source_documents.c.content_text.is_not(None),
    )


def _run_select() -> sa.Select[Any]:
    """Return the select of the intake run columns the API shows."""
    return sa.select(
        intake_runs.c.id,
        intake_runs.c.project_id,
        intake_runs.c.status,
        intake_runs.c.model,
        intake_runs.c.items_extracted,
        intake_runs.c.error,
        intake_runs.c.created_at,
        intake_runs.c.started_at,
        intake_runs.c.finished_at,
    )


def _to_run(row: sa.Row[Any]) -> IntakeRun:
    """Turn an intake run row into the API model."""
    return IntakeRun.model_validate(dict(row._mapping))


def _to_item(row: sa.Row[Any]) -> ScopeItem:
    """Turn a scope item row into the API model."""
    return ScopeItem.model_validate(dict(row._mapping))


class IntakeRepository:
    """User-facing queries: everything runs under the caller's RLS session."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        """Tell whether the caller can see the project."""
        stmt = sa.select(projects.c.id).where(projects.c.id == project_id)
        return (await self._conn.execute(stmt)).first() is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        """Tell whether the caller may change the project."""
        return await access.can_write_project(self._conn, project_id)

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        """Return the project's active intake run, flagged stale after `stale_after_s`, or None."""
        stmt = (
            sa.select(intake_runs.c.id, started_before(intake_runs, stale_after_s).label("stale"))
            .where(intake_runs.c.project_id == project_id, intake_runs.c.status.in_(ACTIVE))
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return ActiveRun(id=row.id, stale=bool(row.stale)) if row else None

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        """Mark an active intake run failed with the error shown to the user."""
        await self._conn.execute(
            sa.update(intake_runs)
            .where(intake_runs.c.id == run_id, intake_runs.c.status.in_(ACTIVE))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def has_readable_upload(self, project_id: uuid.UUID) -> bool:
        """Whether an upload's text was read, so a run can start without pasted notes."""
        stmt = sa.select(source_documents.c.id).where(readable_uploads(project_id)).limit(1)
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
                sa.insert(intake_runs).values(
                    id=run_id, project_id=project_id, status=JobStatus.QUEUED
                )
            )
        except IntegrityError as exc:
            if pg_error_info(exc).sqlstate == "23505":
                raise ActiveRunExistsError from exc
            raise
        if text:
            await self._conn.execute(
                sa.insert(source_documents).values(
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
        """Return one of the project's intake runs, or None."""
        stmt = _run_select().where(
            intake_runs.c.project_id == project_id, intake_runs.c.id == run_id
        )
        row = (await self._conn.execute(stmt)).first()
        return _to_run(row) if row else None

    async def list_runs(self, project_id: uuid.UUID) -> list[IntakeRun]:
        """The project's most recent runs, newest first ("Previous Intakes")."""
        stmt = (
            _run_select()
            .where(intake_runs.c.project_id == project_id)
            .order_by(intake_runs.c.created_at.desc(), intake_runs.c.id.desc())
            .limit(RUN_HISTORY_LIMIT)
        )
        return [_to_run(row) for row in (await self._conn.execute(stmt)).all()]

    async def latest(self, project_id: uuid.UUID) -> IntakeRun | None:
        """Return the project's newest intake run, or None."""
        stmt = (
            _run_select()
            .where(intake_runs.c.project_id == project_id)
            .order_by(intake_runs.c.created_at.desc(), intake_runs.c.id.desc())
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
            .where(scope_items.c.project_id == project_id)
            .order_by(scope_items.c.category, scope_items.c.sort_order)
        )
        if not include_removed:
            stmt = stmt.where(scope_items.c.status != ScopeStatus.REMOVED)
        return [_to_item(row) for row in (await self._conn.execute(stmt)).all()]

    async def create_item(
        self, project_id: uuid.UUID, category: ScopeCategory, text: str
    ) -> ScopeItem | None:
        """Add a manual, accepted item at the end of its category.

        Evidence stays null: the item is the user's own statement ("Yours").
        """
        item_id = uuid.uuid4()
        next_order = (
            sa.select(sa.func.coalesce(sa.func.max(scope_items.c.sort_order) + 1, 0))
            .where(scope_items.c.project_id == project_id, scope_items.c.category == category)
            .scalar_subquery()
        )
        await self._conn.execute(
            sa.insert(scope_items).values(
                id=item_id,
                project_id=project_id,
                category=category,
                text=text,
                status=ScopeStatus.ACCEPTED,
                origin=ItemOrigin.MANUAL,
                sort_order=next_order,
            )
        )
        stmt = sa.select(*_ITEM_COLUMNS).where(scope_items.c.id == item_id)
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
                takes_suggestion = scope_items.c.suggested_text == new_text
                values["evidence"] = sa.case(
                    (takes_suggestion, sa.literal(Evidence.STATED.value)),
                    else_=scope_items.c.evidence,
                )
                values["evidence_note"] = sa.case(
                    (takes_suggestion, sa.null()), else_=scope_items.c.evidence_note
                )
                values["suggested_text"] = sa.case(
                    (takes_suggestion, sa.null()), else_=scope_items.c.suggested_text
                )
            await self._conn.execute(
                sa.update(scope_items).where(scope_items.c.id == item_id).values(**values)
            )
        stmt = sa.select(*_ITEM_COLUMNS).where(scope_items.c.id == item_id)
        row = (await self._conn.execute(stmt)).first()
        return _to_item(row) if row else None
