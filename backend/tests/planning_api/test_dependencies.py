"""Tests of the dependency endpoints and the cycle check.

Purpose: Prove dependencies can be created, listed, changed and deleted, link only work packages of
their own project (SEC-001) and are refused with the cycle named when they would close one
(WBS-009).
How it fits: Exercises app/api/v1/planning.py with the fake planning repository and the pure
`cycle_path`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.common import OrgRole
from app.domain.planning import (
    cycle_path,
)
from tests.support.planning import PlanStore
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth


def test_dependencies_crud(
    plan_client: TestClient, store: Store, plan_store: PlanStore, auth: Auth
) -> None:
    """Dependencies are created, listed, changed and deleted; viewers only read them."""
    user, org, project_id = create_project(plan_client, store, auth)
    headers = auth(user)
    first, second = uuid.uuid4(), uuid.uuid4()
    plan_store.nodes = {
        first: (uuid.UUID(project_id), "1.1 Design"),
        second: (uuid.UUID(project_id), "1.2 Build"),
    }
    url = f"/api/v1/projects/{project_id}/dependencies"

    response = plan_client.post(
        url, json={"predecessorId": str(first), "successorId": str(second)}, headers=headers
    )
    assert response.status_code == 201, response.text
    dep = response.json()
    assert dep == {
        "id": dep["id"],
        "predecessorId": str(first),
        "successorId": str(second),
        "predecessorLabel": "1.1 Design",
        "successorLabel": "1.2 Build",
        "type": "FS",
        "lagDays": 0,
        "origin": "manual",
    }
    assert plan_client.get(url, headers=headers).json() == [dep]

    response = plan_client.patch(
        f"/api/v1/dependencies/{dep['id']}", json={"type": "SS", "lagDays": 2}, headers=headers
    )
    assert (response.status_code, response.json()["type"], response.json()["lagDays"]) == (
        200,
        "SS",
        2,
    )
    assert (
        plan_client.patch(f"/api/v1/dependencies/{dep['id']}", json={}, headers=headers).status_code
        == 422
    )
    assert (
        plan_client.patch(
            f"/api/v1/dependencies/{dep['id']}", json={"type": None}, headers=headers
        ).status_code
        == 422
    )

    response = plan_client.post(
        url, json={"predecessorId": str(first), "successorId": str(uuid.uuid4())}, headers=headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Work package not found in this project"
    response = plan_client.post(
        url, json={"predecessorId": str(first), "successorId": str(first)}, headers=headers
    )
    assert response.status_code == 422
    response = plan_client.post(
        url,
        json={"predecessorId": str(first), "successorId": str(second), "type": "XX"},
        headers=headers,
    )
    assert response.status_code == 422

    viewer = store.add_user("Vic")
    store.members[(org, viewer)] = OrgRole.VIEWER
    hv = auth(viewer)
    assert plan_client.get(url, headers=hv).status_code == 200
    assert (
        plan_client.post(
            url, json={"predecessorId": str(second), "successorId": str(first)}, headers=hv
        ).status_code
        == 403
    )
    assert plan_client.delete(f"/api/v1/dependencies/{dep['id']}", headers=hv).status_code == 403

    assert (
        plan_client.delete(f"/api/v1/dependencies/{dep['id']}", headers=headers).status_code == 204
    )
    assert (
        plan_client.delete(f"/api/v1/dependencies/{dep['id']}", headers=headers).status_code == 404
    )
    assert plan_client.get(url, headers=headers).json() == []


def test_dependency_nodes_must_be_this_projects(
    plan_client: TestClient, store: Store, plan_store: PlanStore, auth: Auth
) -> None:
    """SEC-001: another project's package gets the same 404 as an unknown id, on POST and PATCH."""
    _, _, project_id = create_project(plan_client, store, auth)
    stranger, _, other_pid = create_project(plan_client, store, auth)
    own, foreign = uuid.uuid4(), uuid.uuid4()
    plan_store.nodes = {
        own: (uuid.UUID(project_id), "1.1 Design"),
        foreign: (uuid.UUID(other_pid), "1.1 Theirs"),
    }
    url = f"/api/v1/projects/{other_pid}/dependencies"
    answers = [
        plan_client.post(
            url,
            json={"predecessorId": str(own), "successorId": str(target)},
            headers=auth(stranger),
        )
        for target in (foreign, uuid.uuid4())
    ]
    assert [(answer.status_code, answer.json()["detail"]) for answer in answers] == [
        (404, "Work package not found in this project")
    ] * 2
    assert plan_store.deps == {}

    smuggled = uuid.uuid4()
    plan_store.deps[smuggled] = {
        "id": smuggled,
        "project_id": uuid.UUID(other_pid),
        "predecessor_id": own,
        "successor_id": foreign,
        "type": "FS",
        "lag_days": 0,
        "origin": "manual",
    }
    patched = plan_client.patch(
        f"/api/v1/dependencies/{smuggled}", json={"lagDays": 3}, headers=auth(stranger)
    )
    assert (patched.status_code, plan_store.deps[smuggled]["lag_days"]) == (404, 0)


def test_a_cycle_is_refused_with_its_path(
    plan_client: TestClient, store: Store, plan_store: PlanStore, auth: Auth
) -> None:
    """WBS-009: the 422 names the packages that would form the cycle."""
    user, _, project_id = create_project(plan_client, store, auth)
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    plan_store.nodes = {
        first: (uuid.UUID(project_id), "1.1.3 W03 Ingestion"),
        second: (uuid.UUID(project_id), "1.2.1 W05 Mapping"),
        third: (uuid.UUID(project_id), "1.2.2 W06 Reports"),
    }
    url = f"/api/v1/projects/{project_id}/dependencies"
    for before, after in ((first, second), (second, third)):
        body = {"predecessorId": str(before), "successorId": str(after)}
        assert plan_client.post(url, json=body, headers=auth(user)).status_code == 201

    body = {"predecessorId": str(third), "successorId": str(first)}
    refused = plan_client.post(url, json=body, headers=auth(user))

    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "Dependency would create a cycle: 1.2.2 W06 Reports → 1.1.3 W03 Ingestion → "
        "1.2.1 W05 Mapping → 1.2.2 W06 Reports",
    )
    assert len(plan_store.deps) == 2


def test_cycle_path() -> None:
    """`cycle_path` names the shortest cycle a new link would close, or None."""
    first, second, third, other = (uuid.uuid4() for _ in range(4))
    links = [(first, second), (second, third), (other, first)]
    assert cycle_path(links, second, first) == [second, first, second]
    assert cycle_path(links, third, first) == [third, first, second, third]
    assert cycle_path(links, first, third) is None
    assert cycle_path([], first, second) is None
