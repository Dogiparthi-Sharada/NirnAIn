"""Moved test support."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.ai.evidence import ScopeSuggestion
from app.ai.intake import Source
from app.domain.common import (
    DocumentKind,
    Evidence,
    ItemOrigin,
    JobStatus,
    ScopeCategory,
    ScopeStatus,
)
from app.repositories.intake import ActiveRunExistsError, IntakeRepository
from app.repositories.intake_jobs import ClaimedRun, IntakeJobStore
from tests.support.clock import NOW
from tests.support.connections import Conn, Result, Rows


# --------------------------------------------------------------------------- repository SQL
class _UniqueError(Exception):
    sqlstate = "23505"


class ManyConn(Conn):
    """Also accepts executemany-style parameter lists."""

    def __init__(self) -> None:
        super().__init__()
        self.many: list[list[dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: Any = None) -> Result:
        if params is not None:
            self.many.append(params)
        return await super().execute(stmt)


class RaisingConn(Conn):
    async def execute(self, stmt: Any) -> Result:
        raise IntegrityError("INSERT", {}, _UniqueError())


async def test_list_scope_items_leaves_out_removed_ones_unless_asked() -> None:
    conn = Conn(Rows([]), Rows([]))
    repository = IntakeRepository(conn)  # type: ignore[arg-type]
    await repository.list_scope_items(uuid.uuid4())
    await repository.list_scope_items(uuid.uuid4(), include_removed=True)
    assert "public.scope_items.status !=" in conn.sql[0]
    assert "public.scope_items.status" not in conn.sql[1].split("WHERE", 1)[1]


async def test_create_inserts_run_first_and_maps_unique_violation() -> None:
    conn = Conn()
    await IntakeRepository(conn).create(uuid.uuid4(), "notes")  # type: ignore[arg-type]
    assert conn.sql[0].startswith("INSERT INTO public.intake_runs")
    assert conn.sql[1].startswith("INSERT INTO public.source_documents")
    with pytest.raises(ActiveRunExistsError):
        await IntakeRepository(RaisingConn()).create(uuid.uuid4(), "notes")  # type: ignore[arg-type]


async def test_create_without_pasted_text_stores_no_notes_document() -> None:
    conn = Conn()
    await IntakeRepository(conn).create(uuid.uuid4(), "")  # type: ignore[arg-type]
    assert len(conn.sql) == 1
    assert conn.sql[0].startswith("INSERT INTO public.intake_runs")


async def test_claim_reads_the_runs_pasted_notes_and_readable_uploads() -> None:
    project_id, upload_id = uuid.uuid4(), uuid.uuid4()
    conn = Conn(
        Rows([{"project_id": project_id, "created_at": NOW}]),
        Rows([{"content_text": "Kickoff notes"}]),
        Rows([{"id": upload_id, "title": "brief.pdf", "content_text": "Budget $375K"}]),
    )
    claimed = await IntakeJobStore(conn).claim(uuid.uuid4())  # type: ignore[arg-type]
    assert claimed == ClaimedRun(
        project_id,
        [
            Source(None, "Pasted notes", "Kickoff notes"),
            Source(upload_id, "brief.pdf", "Budget $375K"),
        ],
    )
    claim, pasted, uploads = conn.sql
    assert claim.startswith("UPDATE public.intake_runs")
    assert "public.source_documents.created_at = " in pasted
    assert conn.params[1]["created_at_1"] == NOW
    assert conn.params[1]["kind_1"] == DocumentKind.TEXT
    for fragment in (
        "public.source_documents.kind != ",
        "public.source_documents.extraction_status = ",
        "public.source_documents.content_text IS NOT NULL",
    ):
        assert fragment in uploads
    assert conn.params[2]["extraction_status_1"] == JobStatus.SUCCEEDED


async def test_claim_of_a_run_that_is_not_queued() -> None:
    assert await IntakeJobStore(Conn(Rows([]))).claim(uuid.uuid4()) is None  # type: ignore[arg-type]


async def test_create_item_appends_manual_accepted() -> None:
    pid = uuid.uuid4()
    row = {
        "id": uuid.uuid4(),
        "project_id": pid,
        "category": "risk",
        "text": "Vendor delay",
        "status": "accepted",
        "origin": "manual",
        "confidence": None,
        "source_quote": None,
        "evidence": None,
        "evidence_note": None,
        "suggested_text": None,
        "source_document_id": None,
        "sort_order": 3,
        "created_at": NOW,
        "updated_at": NOW,
    }
    conn = Conn(Result([]), Rows([row]))
    item = await IntakeRepository(conn).create_item(pid, ScopeCategory.RISK, "Vendor delay")  # type: ignore[arg-type]
    assert item is not None
    assert item.sort_order == 3
    assert conn.sql[0].startswith("INSERT INTO public.scope_items")
    assert "max(public.scope_items.sort_order)" in conn.sql[0]
    assert conn.params[0]["status"] == ScopeStatus.ACCEPTED
    assert conn.params[0]["origin"] == ItemOrigin.MANUAL


async def test_list_runs_is_newest_first_and_capped() -> None:
    conn = Conn(Rows([]))
    assert await IntakeRepository(conn).list_runs(uuid.uuid4()) == []  # type: ignore[arg-type]
    assert "ORDER BY public.intake_runs.created_at DESC" in conn.sql[0]
    assert conn.params[0]["param_1"] == 20


async def test_using_the_suggested_text_makes_the_item_stated() -> None:
    conn = Conn(Result([]), Rows([]))
    patch = {"text": "Vendor delay"}
    assert await IntakeRepository(conn).update_item(uuid.uuid4(), patch) is None  # type: ignore[arg-type]
    update = conn.sql[0]
    assert update.startswith("UPDATE public.scope_items SET")
    takes_suggestion = (
        "CASE WHEN (public.scope_items.suggested_text = %(suggested_text_1)s::VARCHAR)"
    )
    for column, otherwise in (
        ("evidence", "public.scope_items.evidence"),
        ("evidence_note", "public.scope_items.evidence_note"),
        ("suggested_text", "public.scope_items.suggested_text"),
    ):
        assert f"{column}={takes_suggestion} THEN" in update
        assert f"ELSE {otherwise} END" in update
    assert conn.params[0]["suggested_text_1"] == "Vendor delay"
    assert conn.params[0]["param_1"] == "stated"
    assert conn.params[0]["text"] == "Vendor delay"

    status_only = Conn(Result([]), Rows([]))
    await IntakeRepository(status_only).update_item(uuid.uuid4(), {"status": "accepted"})  # type: ignore[arg-type]
    assert "CASE" not in status_only.sql[0]


async def test_finish_replaces_untouched_ai_items_and_stores_evidence() -> None:
    conn = ManyConn()
    run_id, project_id, upload_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    suggestion = ScopeSuggestion(
        category=ScopeCategory.RISK,
        text="Vendor delay of 3 months",
        confidence=0.8,
        source_quote="the vendor may slip",
        evidence=Evidence.FLAGGED,
        evidence_note="The notes give no length for the delay.",
        suggested_text="Vendor delay",
        source_document_id=upload_id,
    )
    await IntakeJobStore(conn).finish(run_id, project_id, "m", [suggestion], 1, 2)  # type: ignore[arg-type]
    delete, insert, update = conn.sql
    assert delete.startswith("DELETE FROM public.scope_items")
    for fragment in (
        "public.scope_items.origin =",
        "public.scope_items.status =",
        "public.scope_items.intake_run_id IS DISTINCT FROM",
        "public.scope_items.text = public.scope_items.original_text",
        "public.scope_items.updated_at = public.scope_items.created_at",
    ):
        assert fragment in delete
    assert conn.params[0]["origin_1"] == ItemOrigin.AI
    assert conn.params[0]["status_1"] == ScopeStatus.PENDING
    assert insert.startswith("INSERT INTO public.scope_items")
    ((row,),) = conn.many
    assert row["original_text"] == row["text"] == "Vendor delay of 3 months"
    assert (row["source_quote"], row["evidence"], row["suggested_text"]) == (
        "the vendor may slip",
        Evidence.FLAGGED,
        "Vendor delay",
    )
    assert row["evidence_note"] == "The notes give no length for the delay."
    assert row["source_document_id"] == upload_id
    assert update.startswith("UPDATE public.intake_runs")


async def test_finish_with_no_items_still_clears_stale_suggestions() -> None:
    conn = ManyConn()
    await IntakeJobStore(conn).finish(uuid.uuid4(), uuid.uuid4(), "m", [], 1, 2)  # type: ignore[arg-type]
    assert conn.sql[0].startswith("DELETE FROM public.scope_items")
    assert conn.sql[1].startswith("UPDATE public.intake_runs")
