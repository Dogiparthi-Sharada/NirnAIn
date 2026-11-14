"""Endpoint behaviour with real JWT verification and in-memory repositories."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.domain.common import NotificationLevel, OrgRole
from app.domain.notifications import Notification
from tests.support.projects import post_project, user_with_org
from tests.support.store import Store
from tests.support.tokens import Auth, TokenFactory


# --------------------------------------------------------------------------- auth
def test_requires_bearer_token(client: TestClient) -> None:
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert r.headers["content-type"] == "application/problem+json"


def test_rejects_invalid_token(client: TestClient, make_token: TokenFactory) -> None:
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {make_token(aud='anon')}"})
    assert r.status_code == 401
    assert "Bearer" not in r.text  # token never echoed


# --------------------------------------------------------------------------- me
def test_get_me(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    r = client.get("/api/v1/me", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(user)
    assert body["fullName"] == "Jane Doe"
    assert set(body) == {"id", "email", "fullName", "avatarUrl", "jobTitle", "organizations"}
    assert body["organizations"] == [
        {"id": str(org), "name": "Acme", "slug": "acme", "role": "owner"}
    ]


def test_get_me_without_profile_is_404(client: TestClient, auth: Auth) -> None:
    assert client.get("/api/v1/me", headers=auth(uuid.uuid4())).status_code == 404


def test_patch_me(client: TestClient, store: Store, auth: Auth) -> None:
    user, _ = user_with_org(store)
    r = client.patch(
        "/api/v1/me", json={"fullName": "  Jane Q. Doe ", "jobTitle": "PMO"}, headers=auth(user)
    )
    assert r.status_code == 200
    assert r.json()["fullName"] == "Jane Q. Doe"
    assert r.json()["jobTitle"] == "PMO"


def test_patch_me_validation(client: TestClient, store: Store, auth: Auth) -> None:
    user, _ = user_with_org(store)
    assert client.patch("/api/v1/me", json={}, headers=auth(user)).status_code == 422
    r = client.patch("/api/v1/me", json={"email": "x@y.z"}, headers=auth(user))
    assert r.status_code == 422
    body = r.json()
    assert body["type"] == "urn:problem-type:validation-error"
    assert body["errors"][0]["loc"] == ["body", "email"]
    assert "input" not in body["errors"][0]
    r = client.patch("/api/v1/me", json={"jobTitle": "x" * 121}, headers=auth(user))
    assert r.status_code == 422


# --------------------------------------------------------------------------- dashboard/members
def test_dashboard(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    a = post_project(client, auth, user, org, budgetCents=100).json()["id"]
    b = post_project(client, auth, user, org, budgetCents=200).json()["id"]
    c = post_project(client, auth, user, org, budgetCents=400).json()["id"]
    for pid, status in ((a, "on_track"), (b, "at_risk"), (c, "archived")):
        client.patch(f"/api/v1/projects/{pid}", json={"status": status}, headers=auth(user))
    r = client.get(f"/api/v1/orgs/{org}/dashboard", headers=auth(user))
    assert r.status_code == 200
    assert r.json() == {
        "totalProjects": 2,
        "onTrack": 1,
        "atRiskOrDelayed": 1,
        "totalBudgetCents": 300,
        "createdThisMonth": 2,
    }


def test_members(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    pm = store.add_user("Mark Chen")
    store.members[(org, pm)] = OrgRole.PM
    r = client.get(f"/api/v1/orgs/{org}/members", headers=auth(user))
    assert r.status_code == 200
    roles = {m["fullName"]: m["role"] for m in r.json()}
    assert roles == {"Jane Doe": "owner", "Mark Chen": "pm"}
    assert "joinedAt" in r.json()[0]
    assert client.get(f"/api/v1/orgs/{org}/members", headers=auth(pm)).status_code == 200
    stranger = store.add_user("Eve")
    assert client.get(f"/api/v1/orgs/{org}/members", headers=auth(stranger)).status_code == 404


# --------------------------------------------------------------------------- notifications
def _notify(store: Store, user: uuid.UUID, org: uuid.UUID, minutes_ago: int, read: bool) -> str:
    nid = uuid.uuid4()
    now = datetime.now(UTC)
    store.notifications[nid] = (
        user,
        Notification(
            id=nid,
            org_id=org,
            project_id=None,
            level=NotificationLevel.WARN,
            message=f"n{minutes_ago}",
            read_at=now if read else None,
            created_at=now - timedelta(minutes=minutes_ago),
        ),
    )
    return str(nid)


def test_notifications(client: TestClient, store: Store, auth: Auth) -> None:
    user, org = user_with_org(store)
    n1 = _notify(store, user, org, 1, read=False)
    _notify(store, user, org, 2, read=True)
    other = store.add_user("Bob")
    foreign = _notify(store, other, org, 3, read=False)

    r = client.get("/api/v1/notifications", headers=auth(user))
    assert [n["message"] for n in r.json()["items"]] == ["n1", "n2"]
    r = client.get("/api/v1/notifications", params={"unread": "true"}, headers=auth(user))
    assert [n["id"] for n in r.json()["items"]] == [n1]
    assert r.json()["items"][0]["level"] == "warn"

    r = client.post(f"/api/v1/notifications/{n1}/read", headers=auth(user))
    assert r.status_code == 200
    first_read = r.json()["readAt"]
    assert first_read
    # idempotent
    r = client.post(f"/api/v1/notifications/{n1}/read", headers=auth(user))
    assert r.json()["readAt"] == first_read

    r = client.post(f"/api/v1/notifications/{foreign}/read", headers=auth(user))
    assert r.status_code == 404
