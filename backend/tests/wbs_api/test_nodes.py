"""Tests of the WBS node endpoints: building, validating, editing, moving and deleting the tree.

Purpose: Prove nodes get codes from their tree position, the three-level shape and one root are
enforced, owners must be active employees, viewers only read, and moves and deletes keep the tree
consistent.
How it fits: Exercises app/api/v1/wbs.py with the fake WBS repository of tests/wbs_api/conftest.py,
and the pure `reorder`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.domain.common import OrgRole
from app.domain.wbs import reorder
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth
from tests.support.wbs import NODE_KEYS, WbsStore, add_node


def test_create_tree_and_list_in_order(
    wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth
) -> None:
    """Nodes added under their parents are listed in tree order with codes 1, 1.1, 1.1.1."""
    user, org, project_id = create_project(wbs_client, store, auth)
    headers = auth(user)
    assert wbs_client.get(f"/api/v1/projects/{project_id}/wbs", headers=headers).json() == []

    employee_id = uuid.uuid4()
    wbs_store.employees[employee_id] = (org, "Mark Chen", True)
    root = add_node(wbs_client, project_id, headers, parentId=None, name="CRM")
    assert set(root) == NODE_KEYS
    assert (root["level"], root["code"], root["sortOrder"]) == (1, "1", 0)
    d1 = add_node(wbs_client, project_id, headers, parentId=root["id"], name="Design")
    d2 = add_node(wbs_client, project_id, headers, parentId=root["id"], name="Build")
    wp = add_node(
        wbs_client,
        project_id,
        headers,
        parentId=d1["id"],
        name="Wireframes",
        durationDays=10,
        ownerEmployeeId=str(employee_id),
        description="Low fidelity",
    )
    assert (wp["level"], wp["code"], wp["ownerName"], wp["durationDays"]) == (
        3,
        "1.1.1",
        "Mark Chen",
        10,
    )
    assert d2["sortOrder"] == 1

    response = wbs_client.get(f"/api/v1/projects/{project_id}/wbs", headers=headers)
    assert response.status_code == 200
    assert [(node["code"], node["name"]) for node in response.json()] == [
        ("1", "CRM"),
        ("1.1", "Design"),
        ("1.1.1", "Wireframes"),
        ("1.2", "Build"),
    ]


def test_create_validation(
    wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth
) -> None:
    """A second root is 409; level-4 nodes, unknown parents, foreign owners, bad bodies are 422."""
    user, _, project_id = create_project(wbs_client, store, auth)
    headers = auth(user)
    url = f"/api/v1/projects/{project_id}/wbs/nodes"
    root = add_node(wbs_client, project_id, headers, name="CRM")
    assert wbs_client.post(url, json={"name": "Second root"}, headers=headers).status_code == 409

    deliverable = add_node(wbs_client, project_id, headers, parentId=root["id"], name="D")
    wp = add_node(wbs_client, project_id, headers, parentId=deliverable["id"], name="WP")
    response = wbs_client.post(
        url, json={"parentId": wp["id"], "name": "Too deep"}, headers=headers
    )
    assert response.status_code == 422
    assert "level 3" in response.json()["detail"]
    response = wbs_client.post(
        url, json={"parentId": str(uuid.uuid4()), "name": "X"}, headers=headers
    )
    assert response.status_code == 422

    other = uuid.uuid4()
    wbs_store.employees[other] = (uuid.uuid4(), "Elsewhere", True)
    response = wbs_client.post(
        url,
        json={"parentId": root["id"], "name": "X", "ownerEmployeeId": str(other)},
        headers=headers,
    )
    assert response.status_code == 422
    assert "ownerEmployeeId" in response.json()["detail"]

    for bad in ({"name": ""}, {"name": "X", "durationDays": 3651}, {"name": "X", "extra": 1}):
        assert wbs_client.post(url, json=bad, headers=headers).status_code == 422


def test_permissions(wbs_client: TestClient, store: Store, auth: Auth) -> None:
    """Viewers read but can't add, edit or delete nodes (403); outsiders 404; anonymous 401."""
    owner, org, project_id = create_project(wbs_client, store, auth)
    root = add_node(wbs_client, project_id, auth(owner), name="CRM")
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    viewer_headers = auth(viewer)
    assert (
        wbs_client.get(f"/api/v1/projects/{project_id}/wbs", headers=viewer_headers).status_code
        == 200
    )
    response = wbs_client.post(
        f"/api/v1/projects/{project_id}/wbs/nodes",
        json={"parentId": root["id"], "name": "X"},
        headers=viewer_headers,
    )
    assert response.status_code == 403
    assert (
        wbs_client.patch(
            f"/api/v1/wbs/nodes/{root['id']}", json={"name": "Y"}, headers=viewer_headers
        ).status_code
        == 403
    )
    assert (
        wbs_client.delete(f"/api/v1/wbs/nodes/{root['id']}", headers=viewer_headers).status_code
        == 403
    )

    outsider = store.add_user("Eve")
    outsider_headers = auth(outsider)
    assert (
        wbs_client.get(f"/api/v1/projects/{project_id}/wbs", headers=outsider_headers).status_code
        == 404
    )
    assert (
        wbs_client.patch(
            f"/api/v1/wbs/nodes/{root['id']}", json={"name": "Y"}, headers=outsider_headers
        ).status_code
        == 404
    )
    assert wbs_client.get(f"/api/v1/projects/{project_id}/wbs").status_code == 401


def test_update_node(wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth) -> None:
    """Edits change name, owner, duration; nulls clear; inactive owners and bad bodies are 422."""
    user, org, project_id = create_project(wbs_client, store, auth)
    headers = auth(user)
    employee_id, inactive = uuid.uuid4(), uuid.uuid4()
    wbs_store.employees[employee_id] = (org, "Mark Chen", True)
    wbs_store.employees[inactive] = (org, "Gone", False)
    root = add_node(wbs_client, project_id, headers, name="CRM")
    node = add_node(
        wbs_client, project_id, headers, parentId=root["id"], name="Design", durationDays=5
    )
    url = f"/api/v1/wbs/nodes/{node['id']}"

    response = wbs_client.patch(
        url, json={"name": "Discovery", "ownerEmployeeId": str(employee_id)}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["name"],
        response.json()["ownerName"],
        response.json()["durationDays"],
    ) == (
        "Discovery",
        "Mark Chen",
        5,
    )
    response = wbs_client.patch(
        url, json={"durationDays": None, "ownerEmployeeId": None}, headers=headers
    )
    assert (response.json()["durationDays"], response.json()["ownerEmployeeId"]) == (None, None)

    assert (
        wbs_client.patch(url, json={"ownerEmployeeId": str(inactive)}, headers=headers).status_code
        == 422
    )
    assert wbs_client.patch(url, json={}, headers=headers).status_code == 422
    assert wbs_client.patch(url, json={"name": None}, headers=headers).status_code == 422
    assert wbs_client.patch(url, json={"durationDays": -1}, headers=headers).status_code == 422
    missing = f"/api/v1/wbs/nodes/{uuid.uuid4()}"
    assert wbs_client.patch(missing, json={"name": "X"}, headers=headers).status_code == 404


def test_update_node_skills(wbs_client: TestClient, store: Store, auth: Auth) -> None:
    """Skills replace the node's list (minimum level 3) without touching its other fields."""
    user, _, project_id = create_project(wbs_client, store, auth)
    headers = auth(user)
    root = add_node(wbs_client, project_id, headers, name="CRM")
    node = add_node(
        wbs_client, project_id, headers, parentId=root["id"], name="Staff sign-in", durationDays=5
    )
    url = f"/api/v1/wbs/nodes/{node['id']}"

    response = wbs_client.patch(
        url, json={"skills": ["Okta SSO", "Security Review"]}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert [(skill["name"], skill["minProficiency"]) for skill in response.json()["skills"]] == [
        ("Okta SSO", 3),
        ("Security Review", 3),
    ]
    assert response.json()["durationDays"] == 5  # the node's own fields are untouched
    response = wbs_client.patch(url, json={"name": "Sign-in", "skills": []}, headers=headers)
    assert (response.json()["name"], response.json()["skills"]) == ("Sign-in", [])
    assert wbs_client.patch(url, json={"skills": None}, headers=headers).status_code == 422
    assert wbs_client.patch(url, json={"skills": [""]}, headers=headers).status_code == 422


def test_move_reorders_siblings(wbs_client: TestClient, store: Store, auth: Auth) -> None:
    """Moving a node renumbers its siblings; positions are clamped and the root can't move."""
    user, _, project_id = create_project(wbs_client, store, auth)
    headers = auth(user)
    root = add_node(wbs_client, project_id, headers, name="CRM")
    node_a, node_b, node_c = (
        add_node(wbs_client, project_id, headers, parentId=root["id"], name=label)
        for label in "ABC"
    )

    response = wbs_client.post(
        f"/api/v1/wbs/nodes/{node_c['id']}/move", json={"position": 0}, headers=headers
    )
    assert response.status_code == 200, response.text
    tree = response.json()
    assert [(node["code"], node["name"], node["sortOrder"]) for node in tree[1:]] == [
        ("1.1", "C", 0),
        ("1.2", "A", 1),
        ("1.3", "B", 2),
    ]
    response = wbs_client.post(
        f"/api/v1/wbs/nodes/{node_c['id']}/move", json={"position": 99}, headers=headers
    )
    assert [node["name"] for node in response.json()[1:]] == ["A", "B", "C"]

    move_root = wbs_client.post(
        f"/api/v1/wbs/nodes/{root['id']}/move", json={"position": 0}, headers=headers
    )
    assert move_root.status_code == 422
    bad = wbs_client.post(
        f"/api/v1/wbs/nodes/{node_a['id']}/move", json={"position": -1}, headers=headers
    )
    assert bad.status_code == 422
    assert node_b["level"] == 2


def test_delete_cascades(wbs_client: TestClient, store: Store, auth: Auth) -> None:
    """Deleting a node deletes its children; deleting it again is 404."""
    user, _, project_id = create_project(wbs_client, store, auth)
    headers = auth(user)
    root = add_node(wbs_client, project_id, headers, name="CRM")
    deliverable = add_node(wbs_client, project_id, headers, parentId=root["id"], name="D")
    add_node(wbs_client, project_id, headers, parentId=deliverable["id"], name="WP")
    response = wbs_client.delete(f"/api/v1/wbs/nodes/{deliverable['id']}", headers=headers)
    assert response.status_code == 204
    assert [
        node["name"]
        for node in wbs_client.get(f"/api/v1/projects/{project_id}/wbs", headers=headers).json()
    ] == ["CRM"]
    assert (
        wbs_client.delete(f"/api/v1/wbs/nodes/{deliverable['id']}", headers=headers).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("position", "expected"),
    [(0, "xabc"), (1, "axbc"), (3, "abcx"), (10, "abcx")],
)
def test_reorder(position: int, expected: str) -> None:
    """`reorder` puts the node at the clamped position among its siblings."""
    ids = {letter: uuid.uuid4() for letter in "abcx"}
    names = {node_id: letter for letter, node_id in ids.items()}
    siblings = [ids["a"], ids["x"], ids["b"], ids["c"]]
    assert (
        "".join(names[ordered_id] for ordered_id in reorder(siblings, ids["x"], position))
        == expected
    )
