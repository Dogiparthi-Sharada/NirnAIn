"""Intake endpoints and repository SQL (PR #18 follow-ups), no database needed."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.ai.evidence import ScopeSuggestion
from app.ai.intake import Source
from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import (
    DocumentKind,
    Evidence,
    ItemOrigin,
    JobStatus,
    OrgRole,
    ScopeCategory,
    ScopeStatus,
)
from app.domain.intake import IntakeRun, ScopeItem
from app.repositories.intake import ActiveRun, ActiveRunExistsError, IntakeRepository
from app.repositories.intake_jobs import ClaimedRun, IntakeJobStore
from tests.support.connections import Conn, Result, Rows
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@dataclass
class IntakeStore:
    runs: dict[uuid.UUID, IntakeRun] = field(default_factory=dict)
    items: dict[uuid.UUID, ScopeItem] = field(default_factory=dict)
    texts: dict[uuid.UUID, str] = field(default_factory=dict)
    race: bool = False
    readable_upload: bool = False


class FakeIntakes:
    def __init__(self, store: Store, data: IntakeStore, user_id: uuid.UUID) -> None:
        self.s, self.d, self.uid = store, data, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        return None

    async def has_readable_upload(self, project_id: uuid.UUID) -> bool:
        return self.d.readable_upload

    async def create(self, project_id: uuid.UUID, text: str) -> uuid.UUID:
        if self.d.race:
            raise ActiveRunExistsError
        run_id = uuid.uuid4()
        self.d.runs[run_id] = IntakeRun(
            id=run_id,
            project_id=project_id,
            status="queued",  # type: ignore[arg-type]
            model=None,
            items_extracted=None,
            error=None,
            created_at=NOW + timedelta(seconds=len(self.d.runs)),
            started_at=None,
            finished_at=None,
        )
        self.d.texts[run_id] = text
        return run_id

    async def list_runs(self, project_id: uuid.UUID) -> list[IntakeRun]:
        runs = [run for run in self.d.runs.values() if run.project_id == project_id]
        return sorted(runs, key=lambda run: run.created_at, reverse=True)

    async def get(self, project_id: uuid.UUID, run_id: uuid.UUID) -> IntakeRun | None:
        run = self.d.runs.get(run_id)
        visible = run and run.project_id == project_id and self._role(project_id) is not None
        return run if visible else None

    async def list_scope_items(
        self, project_id: uuid.UUID, *, include_removed: bool = False
    ) -> list[ScopeItem]:
        return [
            item
            for item in self.d.items.values()
            if item.project_id == project_id
            and (include_removed or item.status != ScopeStatus.REMOVED)
        ]

    async def create_item(
        self, project_id: uuid.UUID, category: ScopeCategory, text: str
    ) -> ScopeItem | None:
        order = sum(1 for i in self.d.items.values() if i.category == category)
        item = ScopeItem(
            id=uuid.uuid4(),
            project_id=project_id,
            category=category,
            text=text,
            status=ScopeStatus.ACCEPTED,
            origin=ItemOrigin.MANUAL,
            confidence=None,
            source_quote=None,
            evidence=None,
            evidence_note=None,
            suggested_text=None,
            source_document_id=None,
            sort_order=order,
            created_at=NOW,
            updated_at=NOW,
        )
        self.d.items[item.id] = item
        return item


class FakeIntaker:
    def __init__(self) -> None:
        self.submitted: list[uuid.UUID] = []

    async def submit(self, run_id: uuid.UUID) -> None:
        self.submitted.append(run_id)


@pytest.fixture
def intake_data() -> IntakeStore:
    return IntakeStore()


@pytest.fixture
def intake_client(app: FastAPI, store: Store, intake_data: IntakeStore) -> Iterator[TestClient]:
    def intakes(principal: CurrentPrincipal) -> FakeIntakes:
        return FakeIntakes(store, intake_data, principal.user_id)

    intaker = FakeIntaker()
    app.dependency_overrides[deps.get_intake_repo] = intakes
    app.dependency_overrides[deps.get_intake_runner] = lambda: intaker
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# --------------------------------------------------------------------------- API
def test_get_run_by_id_follows_location(
    intake_client: TestClient, store: Store, auth: Auth
) -> None:
    user, _, pid = create_project(intake_client, store, auth)
    r = intake_client.post(
        f"/api/v1/projects/{pid}/intake/runs", json={"text": "notes"}, headers=auth(user)
    )
    assert r.status_code == 202
    got = intake_client.get(r.headers["location"], headers=auth(user))
    assert got.status_code == 200
    assert got.json()["id"] == r.json()["id"]
    missing = intake_client.get(
        f"/api/v1/projects/{pid}/intake/runs/{uuid.uuid4()}", headers=auth(user)
    )
    assert missing.status_code == 404
    stranger = store.add_user("Eve")
    assert intake_client.get(r.headers["location"], headers=auth(stranger)).status_code == 404


def test_run_needs_notes_or_a_readable_upload(
    intake_client: TestClient, store: Store, intake_data: IntakeStore, auth: Auth
) -> None:
    user, _, project_id = create_project(intake_client, store, auth)
    url = f"/api/v1/projects/{project_id}/intake/runs"
    for body in ({}, {"text": "   "}):
        response = intake_client.post(url, json=body, headers=auth(user))
        assert response.status_code == 422
        assert response.json()["detail"] == "Paste project notes or upload a document first."
    intake_data.readable_upload = True
    response = intake_client.post(url, json={"text": ""}, headers=auth(user))
    assert response.status_code == 202
    assert intake_data.texts[uuid.UUID(response.json()["id"])] == ""


def test_list_runs_newest_first(intake_client: TestClient, store: Store, auth: Auth) -> None:
    user, _, project_id = create_project(intake_client, store, auth)
    url = f"/api/v1/projects/{project_id}/intake/runs"
    first = intake_client.post(url, json={"text": "a"}, headers=auth(user)).json()["id"]
    second = intake_client.post(url, json={"text": "b"}, headers=auth(user)).json()["id"]
    listed = intake_client.get(url, headers=auth(user))
    assert listed.status_code == 200
    assert [run["id"] for run in listed.json()] == [second, first]
    assert {"model", "itemsExtracted", "status", "createdAt"} <= set(listed.json()[0])
    stranger = store.add_user("Eve")
    assert intake_client.get(url, headers=auth(stranger)).status_code == 404


def test_create_run_conflict_on_race(
    intake_client: TestClient, store: Store, intake_data: IntakeStore, auth: Auth
) -> None:
    user, _, pid = create_project(intake_client, store, auth)
    intake_data.race = True
    r = intake_client.post(
        f"/api/v1/projects/{pid}/intake/runs", json={"text": "notes"}, headers=auth(user)
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "AI intake is already running for this project."


def test_create_scope_item(intake_client: TestClient, store: Store, auth: Auth) -> None:
    user, org, pid = create_project(intake_client, store, auth)
    url = f"/api/v1/projects/{pid}/scope-items"
    r = intake_client.post(
        url, json={"category": "deliverable", "text": "  Data warehouse  "}, headers=auth(user)
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["category"], body["text"], body["status"], body["origin"]) == (
        "deliverable",
        "Data warehouse",
        "accepted",
        "manual",
    )
    assert body["sortOrder"] == 0
    evidence_fields = ("sourceQuote", "evidence", "evidenceNote", "suggestedText")
    assert {key: body[key] for key in evidence_fields} == dict.fromkeys(evidence_fields)
    assert body["sourceDocumentId"] is None

    bad = intake_client.post(url, json={"category": "nope", "text": "x"}, headers=auth(user))
    assert bad.status_code == 422
    empty = intake_client.post(url, json={"category": "risk", "text": "  "}, headers=auth(user))
    assert empty.status_code == 422

    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    r = intake_client.post(url, json={"category": "risk", "text": "x"}, headers=auth(viewer))
    assert r.status_code == 403
    stranger = store.add_user("Eve")
    r = intake_client.post(url, json={"category": "risk", "text": "x"}, headers=auth(stranger))
    assert r.status_code == 404


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


def test_removed_scope_items_are_listed_only_when_asked(
    intake_client: TestClient, intake_data: IntakeStore, store: Store, auth: Auth
) -> None:
    user, _, pid = create_project(intake_client, store, auth)
    url = f"/api/v1/projects/{pid}/scope-items"
    created = intake_client.post(
        url, json={"category": "risk", "text": "Vendor delay"}, headers=auth(user)
    ).json()
    item_id = uuid.UUID(created["id"])
    intake_data.items[item_id] = intake_data.items[item_id].model_copy(
        update={"status": ScopeStatus.REMOVED}
    )
    assert intake_client.get(url, headers=auth(user)).json() == []
    listed = intake_client.get(f"{url}?includeRemoved=true", headers=auth(user)).json()
    assert [(item["id"], item["status"]) for item in listed] == [(str(item_id), "removed")]


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
