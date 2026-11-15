"""AI intake, worker side: claim a run, read its notes and store the extracted scope items.

Purpose: Only the trusted background worker writes AI suggestions; re-analysis replaces earlier AI
suggestions nobody touched, never the PM's accepted, removed or edited items.
How it fits: `IntakeJobStore` runs on a service connection (bypasses RLS) from app/jobs/intake.py,
never for a user request; layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.evidence import ScopeSuggestion
from app.ai.intake import PASTED_TITLE, Source
from app.domain.common import DocumentKind, ItemOrigin, JobStatus, ScopeCategory, ScopeStatus
from app.repositories.intake import ACTIVE, readable_uploads
from app.repositories.job_rows import started_before
from app.repositories.tables import intake_runs, scope_items, source_documents


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    """A run the worker has started: its project and the notes to analyze."""

    project_id: uuid.UUID
    sources: list[Source]


def _unedited() -> sa.ColumnElement[bool]:
    """Return the filter for scope items nobody has edited since the AI wrote them."""
    # Items written before original_text was stored: never updated since insert.
    return sa.or_(
        sa.and_(
            scope_items.c.original_text.is_not(None),
            scope_items.c.text == scope_items.c.original_text,
        ),
        sa.and_(
            scope_items.c.original_text.is_(None),
            scope_items.c.updated_at == scope_items.c.created_at,
        ),
    )


class IntakeJobStore:
    """Writes made by the background worker on a service connection (bypasses RLS)."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the worker's service connection."""
        self._conn = conn

    async def claim(self, run_id: uuid.UUID) -> ClaimedRun | None:
        """Move a queued run to running; returns its project and the notes to analyze.

        The notes are the run's pasted text (the 'text' document written with the
        run, see `IntakeRepository.create`) plus every uploaded document whose text
        was read, oldest first.
        """
        stmt = (
            sa.update(intake_runs)
            .where(intake_runs.c.id == run_id, intake_runs.c.status == JobStatus.QUEUED)
            .values(status=JobStatus.RUNNING, started_at=sa.func.now())
            .returning(intake_runs.c.project_id, intake_runs.c.created_at)
        )
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        project_id = row.project_id
        pasted = (
            await self._conn.execute(
                sa.select(source_documents.c.content_text)
                .where(
                    source_documents.c.project_id == project_id,
                    source_documents.c.kind == DocumentKind.TEXT,
                    source_documents.c.created_at == row.created_at,
                )
                .order_by(source_documents.c.id)
                .limit(1)
            )
        ).first()
        sources = [Source(None, PASTED_TITLE, pasted.content_text)] if pasted else []
        uploads = (
            await self._conn.execute(
                sa.select(
                    source_documents.c.id, source_documents.c.title, source_documents.c.content_text
                )
                .where(readable_uploads(project_id))
                .order_by(source_documents.c.created_at, source_documents.c.id)
            )
        ).all()
        sources += [Source(upload.id, upload.title, upload.content_text) for upload in uploads]
        return ClaimedRun(project_id, sources)

    async def finish(
        self,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        model: str,
        suggestions: Sequence[ScopeSuggestion],
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Store the run's suggestions as pending AI items and mark the run succeeded."""
        # Re-analysis replaces earlier AI suggestions nobody has touched yet;
        # accepted, removed and edited items stay.
        await self._conn.execute(
            sa.delete(scope_items).where(
                scope_items.c.project_id == project_id,
                scope_items.c.origin == ItemOrigin.AI,
                scope_items.c.status == ScopeStatus.PENDING,
                scope_items.c.intake_run_id.is_distinct_from(run_id),
                _unedited(),
            )
        )
        if suggestions:
            next_sort_order: dict[ScopeCategory, int] = {}
            rows = []
            for suggestion in suggestions:
                sort_order = next_sort_order.get(suggestion.category, 0)
                next_sort_order[suggestion.category] = sort_order + 1
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "project_id": project_id,
                        "intake_run_id": run_id,
                        "source_document_id": suggestion.source_document_id,
                        "category": suggestion.category,
                        "text": suggestion.text,
                        "original_text": suggestion.text,
                        "source_quote": suggestion.source_quote,
                        "evidence": suggestion.evidence,
                        "evidence_note": suggestion.evidence_note,
                        "suggested_text": suggestion.suggested_text,
                        "confidence": suggestion.confidence,
                        "status": ScopeStatus.PENDING,
                        "origin": ItemOrigin.AI,
                        "sort_order": sort_order,
                    }
                )
            await self._conn.execute(sa.insert(scope_items), rows)
        await self._conn.execute(
            sa.update(intake_runs)
            .where(intake_runs.c.id == run_id)
            .values(
                status=JobStatus.SUCCEEDED,
                model=model,
                items_extracted=len(suggestions),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finished_at=sa.func.now(),
            )
        )

    async def fail(self, run_id: uuid.UUID, error: str) -> None:
        """Mark an active intake run failed with the error shown to the user."""
        await self._conn.execute(
            sa.update(intake_runs)
            .where(intake_runs.c.id == run_id, intake_runs.c.status.in_(ACTIVE))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        """Fail runs active for over `older_than_s` (orphaned by a restart); return the count."""
        result = await self._conn.execute(
            sa.update(intake_runs)
            .where(intake_runs.c.status.in_(ACTIVE), started_before(intake_runs, older_than_s))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )
        return int(result.rowcount)
