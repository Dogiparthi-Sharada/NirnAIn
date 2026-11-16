"""Tests of the project endpoints: create, list, read, update and delete.

Purpose: Prove projects are created as the web form sends them, validated, paged and searched,
updated within their rules and visible only to their organization, with viewers read-only.
How it fits: Exercises app/api/v1/projects.py with real JWT verification and the in-memory
repositories of tests/support/store.py; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient

from app.domain.common import OrgRole
from tests.support.projects import post_project, user_with_org
from tests.support.store import Store
from tests.support.tokens import Auth


# --------------------------------------------------------------------------- projects
def test_create_and_get_project(client: TestClient, store: Store, auth: Auth) -> None:
    """A created project gets a code, the PM's name, draft status and its Location."""
    user, org = user_with_org(store)
    response = post_project(
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
    assert response.status_code == 201, response.text
    body = response.json()
    assert response.headers["location"] == f"/api/v1/projects/{body['id']}"
    assert body["code"] == "P-001"
    assert body["pmName"] == "Jane Doe"
    assert body["status"] == "draft"
    assert body["currentStep"] == "create"
    assert body["currency"] == "USD"
    assert body["budgetCents"] == 37_500_000
    assert body["createdBy"] == str(user)

    response = client.get(f"/api/v1/projects/{body['id']}", headers=auth(user))
    assert response.status_code == 200
    assert response.json()["name"] == "CRM Migration"


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
    response = client.post(f"/api/v1/orgs/{org}/projects", json=payload, headers=auth(user))
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "planning"
    assert response.json()["currentStep"] == "intake"

    draft = {**payload, "name": "Draft only", "status": "draft", "currentStep": "create"}
    response = client.post(f"/api/v1/orgs/{org}/projects", json=draft, headers=auth(user))
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "draft"


def test_create_project_cannot_start_in_a_later_status(
    client: TestClient, store: Store, auth: Auth
) -> None:
    """A project can't be created in a later status."""
    user, org = user_with_org(store)
    response = post_project(client, auth, user, org, status="completed")
    assert response.status_code == 422


def test_create_project_validation(client: TestClient, store: Store, auth: Auth) -> None:
    """Bad dates, budgets, types, currencies, names and statuses are 422."""
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
        response = post_project(client, auth, user, org, **body)
        assert response.status_code == 422, body


def test_create_project_pm_must_be_member(client: TestClient, store: Store, auth: Auth) -> None:
    """The PM must be a member of the organization."""
    user, org = user_with_org(store)
    response = post_project(client, auth, user, org, pmUserId=str(uuid.uuid4()))
    assert response.status_code == 422
    assert "pmUserId" in response.json()["detail"]


def test_create_project_in_foreign_org_is_404(client: TestClient, store: Store, auth: Auth) -> None:
    """Creating in another organization is 404."""
    user, _ = user_with_org(store)
    other_org = store.add_org(store.add_user("Bob Smith"), "Other")
    assert post_project(client, auth, user, other_org).status_code == 404


def test_viewer_cannot_create(client: TestClient, store: Store, auth: Auth) -> None:
    """Viewers can't create projects (RLS 403)."""
    _owner, org = user_with_org(store)
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    response = post_project(client, auth, viewer, org)
    assert response.status_code == 403  # RLS violation (42501) mapped


def test_list_projects_pagination_and_filters(client: TestClient, store: Store, auth: Auth) -> None:
    """Projects page newest first, hide archived, filter by status and search by name."""
    user, org = user_with_org(store)
    ids = [
        post_project(client, auth, user, org, name=f"Project {number}").json()["id"]
        for number in range(5)
    ]
    archived = ids[0]
    client.patch(f"/api/v1/projects/{archived}", json={"status": "archived"}, headers=auth(user))

    url = f"/api/v1/orgs/{org}/projects"
    response = client.get(url, params={"limit": 2}, headers=auth(user))
    page1 = response.json()
    assert [project["id"] for project in page1["items"]] == [ids[4], ids[3]]
    assert page1["nextCursor"]
    page2 = client.get(
        url, params={"limit": 2, "cursor": page1["nextCursor"]}, headers=auth(user)
    ).json()
    assert [project["id"] for project in page2["items"]] == [ids[2], ids[1]]
    assert page2["nextCursor"] is None  # archived one hidden by default

    response = client.get(url, params={"status": "archived"}, headers=auth(user))
    assert [project["id"] for project in response.json()["items"]] == [archived]

    response = client.get(url, params={"q": "project 3"}, headers=auth(user))
    assert [project["name"] for project in response.json()["items"]] == ["Project 3"]

    assert client.get(url, params={"cursor": "garbage"}, headers=auth(user)).status_code == 400
    assert client.get(url, params={"limit": 0}, headers=auth(user)).status_code == 422
    assert client.get(url, params={"status": "active"}, headers=auth(user)).status_code == 422


def test_list_projects_foreign_org_is_404(client: TestClient, store: Store, auth: Auth) -> None:
    """Listing another organization's projects is 404."""
    user, _ = user_with_org(store)
    response = client.get(f"/api/v1/orgs/{uuid.uuid4()}/projects", headers=auth(user))
    assert response.status_code == 404


def test_patch_project(client: TestClient, store: Store, auth: Auth) -> None:
    """Status, step, progress and sponsor change; dates are checked with stored ones."""
    user, org = user_with_org(store)
    project_id = post_project(client, auth, user, org, startDate="2026-10-01").json()["id"]
    response = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"status": "on_track", "currentStep": "scope", "progressPct": 40, "sponsor": None},
        headers=auth(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["currentStep"], body["progressPct"]) == ("on_track", "scope", 40)

    # merged date validation against the stored startDate
    response = client.patch(
        f"/api/v1/projects/{project_id}", json={"targetEndDate": "2026-01-01"}, headers=auth(user)
    )
    assert response.status_code == 422
    for bad in ({}, {"name": None}, {"progressPct": 101}, {"code": "P-999"}):
        response = client.patch(f"/api/v1/projects/{project_id}", json=bad, headers=auth(user))
        assert response.status_code == 422, bad


def test_patch_project_dates_respect_step_5(client: TestClient, store: Store, auth: Auth) -> None:
    """New project dates are mirrored on step 5, so one that would cross step 5's other saved
    date is refused (422) unless both are set; without a saved step 5 nothing is checked."""
    user, org = user_with_org(store)
    project_id = post_project(client, auth, user, org, startDate="2026-12-01").json()["id"]
    url = f"/api/v1/projects/{project_id}"
    store.step5[uuid.UUID(project_id)] = (date(2026, 12, 1), date(2027, 5, 28))

    response = client.patch(url, json={"startDate": "2027-06-01"}, headers=auth(user))
    assert response.status_code == 422
    assert "set targetEndDate as well" in response.json()["detail"]
    both = {"startDate": "2027-06-01", "targetEndDate": "2027-09-30"}
    assert client.patch(url, json=both, headers=auth(user)).status_code == 200
    store.step5[uuid.UUID(project_id)] = (date(2027, 6, 1), date(2027, 9, 30))
    response = client.patch(url, json={"targetEndDate": "2027-05-01"}, headers=auth(user))
    assert response.status_code == 422  # before the project's own start too
    store.step5.clear()
    response = client.patch(url, json={"startDate": "2027-01-04"}, headers=auth(user))
    assert response.status_code == 200


def test_viewer_cannot_patch_or_delete(client: TestClient, store: Store, auth: Auth) -> None:
    """Viewers read but can't change or delete a project."""
    owner, org = user_with_org(store)
    project_id = post_project(client, auth, owner, org).json()["id"]
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert client.get(f"/api/v1/projects/{project_id}", headers=auth(viewer)).status_code == 200
    response = client.patch(
        f"/api/v1/projects/{project_id}", json={"name": "X"}, headers=auth(viewer)
    )
    assert response.status_code == 403
    assert client.delete(f"/api/v1/projects/{project_id}", headers=auth(viewer)).status_code == 403


def test_delete_project(client: TestClient, store: Store, auth: Auth) -> None:
    """A deleted project is gone; deleting again is 404."""
    user, org = user_with_org(store)
    project_id = post_project(client, auth, user, org).json()["id"]
    response = client.delete(f"/api/v1/projects/{project_id}", headers=auth(user))
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/api/v1/projects/{project_id}", headers=auth(user)).status_code == 404
    assert client.delete(f"/api/v1/projects/{project_id}", headers=auth(user)).status_code == 404


def test_project_invisible_to_outsider(client: TestClient, store: Store, auth: Auth) -> None:
    """Outsiders get 404; a malformed id is 422."""
    user, org = user_with_org(store)
    project_id = post_project(client, auth, user, org).json()["id"]
    outsider = store.add_user("Eve Outsider")
    assert client.get(f"/api/v1/projects/{project_id}", headers=auth(outsider)).status_code == 404
    assert client.get("/api/v1/projects/not-a-uuid", headers=auth(user)).status_code == 422
