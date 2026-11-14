"""Creating a project through the API, the first step of most endpoint tests."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from tests.support.store import Store
from tests.support.tokens import Auth


def create_project(
    client: TestClient, store: Store, auth: Auth, **body: Any
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Create Jane Doe, her organization Acme and project CRM; return (user, org, project id).

    Extra keyword arguments are added to the project's JSON body (camelCase keys).
    """
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    response = client.post(
        f"/api/v1/orgs/{org}/projects", json={"name": "CRM", **body}, headers=auth(user)
    )
    return user, org, response.json()["id"]


def user_with_org(store: Store) -> tuple[uuid.UUID, uuid.UUID]:
    """Add Jane Doe and her organization Acme to the store; return (user, org)."""
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    return user, org


def post_project(
    client: TestClient, auth: Auth, user: uuid.UUID, org: uuid.UUID, **body: object
) -> Response:
    """POST a project named "CRM Migration" (plus `body`) to the organization as `user`."""
    payload = {"name": "CRM Migration", **body}
    return client.post(f"/api/v1/orgs/{org}/projects", json=payload, headers=auth(user))
