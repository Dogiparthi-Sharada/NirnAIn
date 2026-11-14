"""Intake endpoints and repository SQL (PR #18 follow-ups), no database needed."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import (
    OrgRole,
    ScopeStatus,
)
from tests.support.intake import FakeIntaker, FakeIntakes, IntakeStore
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth


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
