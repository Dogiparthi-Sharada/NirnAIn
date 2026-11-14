"""Moved test support."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.common import OrgRole
from tests.support.projects import post_project, user_with_org
from tests.support.store import Store
from tests.support.tokens import Auth


# --------------------------------------------------------------------------- projects
def test_create_and_get_project(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    r = post_project(
        client,
        auth,
        user,
        org,
        type="product_development",
        pmUserId=str(user),
        priority="high",
        methodology="agile",
        visibility="confidential",
        startDate="2026-10-01",
        targetEndDate="2027-03-31",
        budgetCents=37_500_000,
        currency="usd",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert r.headers["location"] == f"/api/v1/projects/{body['id']}"
    assert body["code"] == "P-001"
    assert body["pmName"] == "Jane Doe"
    assert body["status"] == "draft"
    assert body["currentStep"] == "create"
    assert body["currency"] == "USD"
    assert body["budgetCents"] == 37_500_000
    assert body["createdBy"] == str(user)

    r = client.get(f"/api/v1/projects/{body['id']}", headers=auth(user))
    assert r.status_code == 200
    assert r.json()["name"] == "CRM Migration"


def test_create_project_with_the_web_forms_payload(
    client: TestClient, store: Store, auth: Auth
) -> None:
    """Exactly what src/screens/CreateProject.tsx sends for "Continue to AI Intake"."""
    user, org = user_with_org(store)
    payload = {
        "name": "Enterprise Data Platform",
        "description": "Consolidate reporting",
        "type": "technology",
        "department": "Engineering",
        "sponsor": None,
        "pmUserId": str(user),
        "priority": "high",
        "methodology": "waterfall",
        "visibility": "internal",
        "startDate": "2026-11-02",
        "targetEndDate": "2027-06-30",
        "budgetCents": 37_500_000,
        "currency": "USD",
        "status": "planning",
        "currentStep": "intake",
    }
    r = client.post(f"/api/v1/orgs/{org}/projects", json=payload, headers=auth(user))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "planning"
    assert r.json()["currentStep"] == "intake"

    draft = {**payload, "name": "Draft only", "status": "draft", "currentStep": "create"}
    r = client.post(f"/api/v1/orgs/{org}/projects", json=draft, headers=auth(user))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "draft"


def test_create_project_cannot_start_in_a_later_status(
    client: TestClient, store: Store, auth: Auth
) -> None:
    user, org = user_with_org(store)
    r = post_project(client, auth, user, org, status="completed")
    assert r.status_code == 422


def test_create_project_validation(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    bad = [
        {"startDate": "2026-10-01", "targetEndDate": "2026-09-01"},
        {"budgetCents": -1},
        {"type": "unknown"},
        {"currency": "US"},
        {"name": ""},
        {"status": "on_track"},  # not settable on create
    ]
    for body in bad:
        r = post_project(client, auth, user, org, **body)
        assert r.status_code == 422, body


def test_create_project_pm_must_be_member(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    r = post_project(client, auth, user, org, pmUserId=str(uuid.uuid4()))
    assert r.status_code == 422
    assert "pmUserId" in r.json()["detail"]


def test_create_project_in_foreign_org_is_404(client: TestClient, store: Store, auth: Auth) -> None:
    user, _ = user_with_org(store)
    other_org = store.add_org(store.add_user("Bob Smith"), "Other")
    assert post_project(client, auth, user, other_org).status_code == 404


def test_viewer_cannot_create(client: TestClient, store: Store, auth: Auth) -> None:
    _owner, org = user_with_org(store)
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    r = post_project(client, auth, viewer, org)
    assert r.status_code == 403  # RLS violation (42501) mapped


def test_list_projects_pagination_and_filters(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    ids = [
        post_project(client, auth, user, org, name=f"Project {i}").json()["id"] for i in range(5)
    ]
    archived = ids[0]
    client.patch(f"/api/v1/projects/{archived}", json={"status": "archived"}, headers=auth(user))

    url = f"/api/v1/orgs/{org}/projects"
    r = client.get(url, params={"limit": 2}, headers=auth(user))
    page1 = r.json()
    assert [p["id"] for p in page1["items"]] == [ids[4], ids[3]]
    assert page1["nextCursor"]
    page2 = client.get(
        url, params={"limit": 2, "cursor": page1["nextCursor"]}, headers=auth(user)
    ).json()
    assert [p["id"] for p in page2["items"]] == [ids[2], ids[1]]
    assert page2["nextCursor"] is None  # archived one hidden by default

    r = client.get(url, params={"status": "archived"}, headers=auth(user))
    assert [p["id"] for p in r.json()["items"]] == [archived]

    r = client.get(url, params={"q": "project 3"}, headers=auth(user))
    assert [p["name"] for p in r.json()["items"]] == ["Project 3"]

    assert client.get(url, params={"cursor": "garbage"}, headers=auth(user)).status_code == 400
    assert client.get(url, params={"limit": 0}, headers=auth(user)).status_code == 422
    assert client.get(url, params={"status": "active"}, headers=auth(user)).status_code == 422


def test_list_projects_foreign_org_is_404(client: TestClient, store: Store, auth: Auth) -> None:
    user, _ = user_with_org(store)
    r = client.get(f"/api/v1/orgs/{uuid.uuid4()}/projects", headers=auth(user))
    assert r.status_code == 404


def test_patch_project(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    pid = post_project(client, auth, user, org, startDate="2026-10-01").json()["id"]
    r = client.patch(
        f"/api/v1/projects/{pid}",
        json={"status": "on_track", "currentStep": "scope", "progressPct": 40, "sponsor": None},
        headers=auth(user),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["currentStep"], body["progressPct"]) == ("on_track", "scope", 40)

    # merged date validation against the stored startDate
    r = client.patch(
        f"/api/v1/projects/{pid}", json={"targetEndDate": "2026-01-01"}, headers=auth(user)
    )
    assert r.status_code == 422
    for bad in ({}, {"name": None}, {"progressPct": 101}, {"code": "P-999"}):
        r = client.patch(f"/api/v1/projects/{pid}", json=bad, headers=auth(user))
        assert r.status_code == 422, bad


def test_viewer_cannot_patch_or_delete(client: TestClient, store: Store, auth: Auth) -> None:
    owner, org = user_with_org(store)
    pid = post_project(client, auth, owner, org).json()["id"]
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert client.get(f"/api/v1/projects/{pid}", headers=auth(viewer)).status_code == 200
    r = client.patch(f"/api/v1/projects/{pid}", json={"name": "X"}, headers=auth(viewer))
    assert r.status_code == 403
    assert client.delete(f"/api/v1/projects/{pid}", headers=auth(viewer)).status_code == 403


def test_delete_project(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    pid = post_project(client, auth, user, org).json()["id"]
    r = client.delete(f"/api/v1/projects/{pid}", headers=auth(user))
    assert r.status_code == 204
    assert r.content == b""
    assert client.get(f"/api/v1/projects/{pid}", headers=auth(user)).status_code == 404
    assert client.delete(f"/api/v1/projects/{pid}", headers=auth(user)).status_code == 404


def test_project_invisible_to_outsider(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    pid = post_project(client, auth, user, org).json()["id"]
    outsider = store.add_user("Eve Outsider")
    assert client.get(f"/api/v1/projects/{pid}", headers=auth(outsider)).status_code == 404
    assert client.get("/api/v1/projects/not-a-uuid", headers=auth(user)).status_code == 422
