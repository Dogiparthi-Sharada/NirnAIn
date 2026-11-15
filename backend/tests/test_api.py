"""Tests of the account, dashboard, members and notification endpoints.

Purpose: Prove real JWT checks guard the API, the profile can be read and edited within its limits,
the dashboard counts the right projects, and notifications stay private to their user.
How it fits: Exercises app/api/v1/me.py, orgs.py and notifications.py with real JWT verification and
the in-memory repositories of tests/support/store.py; layer: tests.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

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
    """A request without a token is 401 problem+json with a Bearer challenge."""
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert response.headers["content-type"] == "application/problem+json"


def test_rejects_invalid_token(client: TestClient, make_token: TokenFactory) -> None:
    """A token for the wrong audience is 401 and never echoed back."""
    response = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {make_token(aud='anon')}"}
    )
    assert response.status_code == 401
    assert "Bearer" not in response.text  # token never echoed


# --------------------------------------------------------------------------- me
def test_get_me(client: TestClient, store: Store, auth: Auth) -> None:
    """The profile lists the user's fields and organizations with their role."""
    user, org = user_with_org(store)
    response = client.get("/api/v1/me", headers=auth(user))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user)
    assert body["fullName"] == "Jane Doe"
    assert set(body) == {"id", "email", "fullName", "avatarUrl", "jobTitle", "organizations"}
    assert body["organizations"] == [
        {"id": str(org), "name": "Acme", "slug": "acme", "role": "owner"}
    ]


def test_get_me_without_profile_is_404(client: TestClient, auth: Auth) -> None:
    """A valid token without a profile is 404."""
    assert client.get("/api/v1/me", headers=auth(uuid.uuid4())).status_code == 404


def test_patch_me(client: TestClient, store: Store, auth: Auth) -> None:
    """The name (trimmed) and job title can be changed."""
    user, _ = user_with_org(store)
    response = client.patch(
        "/api/v1/me", json={"fullName": "  Jane Q. Doe ", "jobTitle": "PMO"}, headers=auth(user)
    )
    assert response.status_code == 200
    assert response.json()["fullName"] == "Jane Q. Doe"
    assert response.json()["jobTitle"] == "PMO"


def test_patch_me_validation(client: TestClient, store: Store, auth: Auth) -> None:
    """Empty bodies, unknown fields (without echoing input) and long titles are 422."""
    user, _ = user_with_org(store)
    assert client.patch("/api/v1/me", json={}, headers=auth(user)).status_code == 422
    response = client.patch("/api/v1/me", json={"email": "x@y.z"}, headers=auth(user))
    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "urn:problem-type:validation-error"
    assert body["errors"][0]["loc"] == ["body", "email"]
    assert "input" not in body["errors"][0]
    response = client.patch("/api/v1/me", json={"jobTitle": "x" * 121}, headers=auth(user))
    assert response.status_code == 422


# --------------------------------------------------------------------------- dashboard/members
def test_dashboard(client: TestClient, store: Store, auth: Auth) -> None:
    """The dashboard counts and sums the active projects; archived ones are left out."""
    user, org = user_with_org(store)
    first_project = post_project(client, auth, user, org, budgetCents=100).json()["id"]
    second_project = post_project(client, auth, user, org, budgetCents=200).json()["id"]
    archived_project = post_project(client, auth, user, org, budgetCents=400).json()["id"]
    for project_id, status in (
        (first_project, "on_track"),
        (second_project, "at_risk"),
        (archived_project, "archived"),
    ):
        client.patch(f"/api/v1/projects/{project_id}", json={"status": status}, headers=auth(user))
    response = client.get(f"/api/v1/orgs/{org}/dashboard", headers=auth(user))
    assert response.status_code == 200
    assert response.json() == {
        "totalProjects": 2,
        "onTrack": 1,
        "atRiskOrDelayed": 1,
        "totalBudgetCents": 300,
        "createdThisMonth": 2,
    }


def test_members(client: TestClient, store: Store, auth: Auth) -> None:
    """Members see each other's roles; outsiders get 404."""
    user, org = user_with_org(store)
    project_manager = store.add_user("Mark Chen")
    store.members[(org, project_manager)] = OrgRole.PM
    response = client.get(f"/api/v1/orgs/{org}/members", headers=auth(user))
    assert response.status_code == 200
    roles = {member["fullName"]: member["role"] for member in response.json()}
    assert roles == {"Jane Doe": "owner", "Mark Chen": "pm"}
    assert "joinedAt" in response.json()[0]
    assert (
        client.get(f"/api/v1/orgs/{org}/members", headers=auth(project_manager)).status_code == 200
    )
    stranger = store.add_user("Eve")
    assert client.get(f"/api/v1/orgs/{org}/members", headers=auth(stranger)).status_code == 404


# --------------------------------------------------------------------------- notifications
def _notify(store: Store, user: uuid.UUID, org: uuid.UUID, minutes_ago: int, read: bool) -> str:
    """Add a warning notification for the user, created minutes ago and maybe read."""
    notification_id = uuid.uuid4()
    now = datetime.now(UTC)
    store.notifications[notification_id] = (
        user,
        Notification(
            id=notification_id,
            org_id=org,
            project_id=None,
            level=NotificationLevel.WARN,
            message=f"n{minutes_ago}",
            read_at=now if read else None,
            created_at=now - timedelta(minutes=minutes_ago),
        ),
    )
    return str(notification_id)


def test_notifications(client: TestClient, store: Store, auth: Auth) -> None:
    """Notifications list newest first, filter unread, mark read once; others' are 404."""
    user, org = user_with_org(store)
    unread_id = _notify(store, user, org, 1, read=False)
    _notify(store, user, org, 2, read=True)
    other = store.add_user("Bob")
    foreign = _notify(store, other, org, 3, read=False)

    response = client.get("/api/v1/notifications", headers=auth(user))
    assert [notification["message"] for notification in response.json()["items"]] == ["n1", "n2"]
    response = client.get("/api/v1/notifications", params={"unread": "true"}, headers=auth(user))
    assert [notification["id"] for notification in response.json()["items"]] == [unread_id]
    assert response.json()["items"][0]["level"] == "warn"

    response = client.post(f"/api/v1/notifications/{unread_id}/read", headers=auth(user))
    assert response.status_code == 200
    first_read = response.json()["readAt"]
    assert first_read
    # idempotent
    response = client.post(f"/api/v1/notifications/{unread_id}/read", headers=auth(user))
    assert response.json()["readAt"] == first_read

    response = client.post(f"/api/v1/notifications/{foreign}/read", headers=auth(user))
    assert response.status_code == 404
