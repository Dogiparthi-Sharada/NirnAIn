"""AI intake, worker side: claim a run, read its notes, store the extracted items.

`IntakeJobStore` runs on a service connection (see `app.jobs.intake`), which
bypasses RLS: it is only used by the trusted background worker, never for a
user request.
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
from app.repositories.intake import ACTIVE, doc_t, item_t, older_than, readable_uploads, run_t


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    """A run the worker has started: its project and the notes to analyze."""

    project_id: uuid.UUID
    sources: list[Source]


def _unedited() -> sa.ColumnElement[bool]:
    # Items written before original_text was stored: never updated since insert.
    return sa.or_(
        sa.and_(item_t.c.original_text.is_not(None), item_t.c.text == item_t.c.original_text),
        sa.and_(item_t.c.original_text.is_(None), item_t.c.updated_at == item_t.c.created_at),
    )


class IntakeJobStore:
    """Writes made by the background worker on a service connection (bypasses RLS)."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def claim(self, run_id: uuid.UUID) -> ClaimedRun | None:
        """Move a queued run to running; returns its project and the notes to analyze.

        The notes are the run's pasted text (the 'text' document written with the
        run, see `IntakeRepository.create`) plus every uploaded document whose text
        was read, oldest first.
        """
        stmt = (
            sa.update(run_t)
            .where(run_t.c.id == run_id, run_t.c.status == JobStatus.QUEUED)
            .values(status=JobStatus.RUNNING, started_at=sa.func.now())
            .returning(run_t.c.project_id, run_t.c.created_at)
        )
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        project_id = row.project_id
        pasted = (
            await self._conn.execute(
                sa.select(doc_t.c.content_text)
                .where(
                    doc_t.c.project_id == project_id,
                    doc_t.c.kind == DocumentKind.TEXT,
                    doc_t.c.created_at == row.created_at,
                )
                .order_by(doc_t.c.id)
                .limit(1)
            )
        ).first()
        sources = [Source(None, PASTED_TITLE, pasted.content_text)] if pasted else []
        uploads = (
            await self._conn.execute(
                sa.select(doc_t.c.id, doc_t.c.title, doc_t.c.content_text)
                .where(readable_uploads(project_id))
                .order_by(doc_t.c.created_at, doc_t.c.id)
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
            sa.delete(item_t).where(
                item_t.c.project_id == project_id,
                item_t.c.origin == ItemOrigin.AI,
                item_t.c.status == ScopeStatus.PENDING,
                item_t.c.intake_run_id.is_distinct_from(run_id),
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
            await self._conn.execute(sa.insert(item_t), rows)
        await self._conn.execute(
            sa.update(run_t)
            .where(run_t.c.id == run_id)
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
        await self._conn.execute(
            sa.update(run_t)
            .where(run_t.c.id == run_id, run_t.c.status.in_(ACTIVE))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        result = await self._conn.execute(
            sa.update(run_t)
            .where(run_t.c.status.in_(ACTIVE), older_than(older_than_s))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )
        return int(result.rowcount)
