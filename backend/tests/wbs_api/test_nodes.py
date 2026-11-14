"""WBS endpoints with real JWT verification and an in-memory fake repository."""

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
    user, org, pid = create_project(wbs_client, store, auth)
    h = auth(user)
    assert wbs_client.get(f"/api/v1/projects/{pid}/wbs", headers=h).json() == []

    emp = uuid.uuid4()
    wbs_store.employees[emp] = (org, "Mark Chen", True)
    root = add_node(wbs_client, pid, h, parentId=None, name="CRM")
    assert set(root) == NODE_KEYS
    assert (root["level"], root["code"], root["sortOrder"]) == (1, "1", 0)
    d1 = add_node(wbs_client, pid, h, parentId=root["id"], name="Design")
    d2 = add_node(wbs_client, pid, h, parentId=root["id"], name="Build")
    wp = add_node(
        wbs_client,
        pid,
        h,
        parentId=d1["id"],
        name="Wireframes",
        durationDays=10,
        ownerEmployeeId=str(emp),
        description="Low fidelity",
    )
    assert (wp["level"], wp["code"], wp["ownerName"], wp["durationDays"]) == (
        3,
        "1.1.1",
        "Mark Chen",
        10,
    )
    assert d2["sortOrder"] == 1

    r = wbs_client.get(f"/api/v1/projects/{pid}/wbs", headers=h)
    assert r.status_code == 200
    assert [(n["code"], n["name"]) for n in r.json()] == [
        ("1", "CRM"),
        ("1.1", "Design"),
        ("1.1.1", "Wireframes"),
        ("1.2", "Build"),
    ]


def test_create_validation(
    wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth
) -> None:
    user, _, pid = create_project(wbs_client, store, auth)
    h = auth(user)
    url = f"/api/v1/projects/{pid}/wbs/nodes"
    root = add_node(wbs_client, pid, h, name="CRM")
    assert wbs_client.post(url, json={"name": "Second root"}, headers=h).status_code == 409

    d = add_node(wbs_client, pid, h, parentId=root["id"], name="D")
    wp = add_node(wbs_client, pid, h, parentId=d["id"], name="WP")
    r = wbs_client.post(url, json={"parentId": wp["id"], "name": "Too deep"}, headers=h)
    assert r.status_code == 422
    assert "level 3" in r.json()["detail"]
    r = wbs_client.post(url, json={"parentId": str(uuid.uuid4()), "name": "X"}, headers=h)
    assert r.status_code == 422

    other = uuid.uuid4()
    wbs_store.employees[other] = (uuid.uuid4(), "Elsewhere", True)
    r = wbs_client.post(
        url, json={"parentId": root["id"], "name": "X", "ownerEmployeeId": str(other)}, headers=h
    )
    assert r.status_code == 422
    assert "ownerEmployeeId" in r.json()["detail"]

    for bad in ({"name": ""}, {"name": "X", "durationDays": 3651}, {"name": "X", "extra": 1}):
        assert wbs_client.post(url, json=bad, headers=h).status_code == 422


def test_permissions(wbs_client: TestClient, store: Store, auth: Auth) -> None:
    owner, org, pid = create_project(wbs_client, store, auth)
    root = add_node(wbs_client, pid, auth(owner), name="CRM")
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    hv = auth(viewer)
    assert wbs_client.get(f"/api/v1/projects/{pid}/wbs", headers=hv).status_code == 200
    r = wbs_client.post(
        f"/api/v1/projects/{pid}/wbs/nodes", json={"parentId": root["id"], "name": "X"}, headers=hv
    )
    assert r.status_code == 403
    assert (
        wbs_client.patch(
            f"/api/v1/wbs/nodes/{root['id']}", json={"name": "Y"}, headers=hv
        ).status_code
        == 403
    )
    assert wbs_client.delete(f"/api/v1/wbs/nodes/{root['id']}", headers=hv).status_code == 403

    outsider = store.add_user("Eve")
    ho = auth(outsider)
    assert wbs_client.get(f"/api/v1/projects/{pid}/wbs", headers=ho).status_code == 404
    assert (
        wbs_client.patch(
            f"/api/v1/wbs/nodes/{root['id']}", json={"name": "Y"}, headers=ho
        ).status_code
        == 404
    )
    assert wbs_client.get(f"/api/v1/projects/{pid}/wbs").status_code == 401


def test_update_node(wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth) -> None:
    user, org, pid = create_project(wbs_client, store, auth)
    h = auth(user)
    emp, inactive = uuid.uuid4(), uuid.uuid4()
    wbs_store.employees[emp] = (org, "Mark Chen", True)
    wbs_store.employees[inactive] = (org, "Gone", False)
    root = add_node(wbs_client, pid, h, name="CRM")
    node = add_node(wbs_client, pid, h, parentId=root["id"], name="Design", durationDays=5)
    url = f"/api/v1/wbs/nodes/{node['id']}"

    r = wbs_client.patch(url, json={"name": "Discovery", "ownerEmployeeId": str(emp)}, headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["ownerName"], r.json()["durationDays"]) == (
        "Discovery",
        "Mark Chen",
        5,
    )
    r = wbs_client.patch(url, json={"durationDays": None, "ownerEmployeeId": None}, headers=h)
    assert (r.json()["durationDays"], r.json()["ownerEmployeeId"]) == (None, None)

    assert (
        wbs_client.patch(url, json={"ownerEmployeeId": str(inactive)}, headers=h).status_code == 422
    )
    assert wbs_client.patch(url, json={}, headers=h).status_code == 422
    assert wbs_client.patch(url, json={"name": None}, headers=h).status_code == 422
    assert wbs_client.patch(url, json={"durationDays": -1}, headers=h).status_code == 422
    missing = f"/api/v1/wbs/nodes/{uuid.uuid4()}"
    assert wbs_client.patch(missing, json={"name": "X"}, headers=h).status_code == 404


def test_update_node_skills(wbs_client: TestClient, store: Store, auth: Auth) -> None:
    user, _, pid = create_project(wbs_client, store, auth)
    h = auth(user)
    root = add_node(wbs_client, pid, h, name="CRM")
    node = add_node(wbs_client, pid, h, parentId=root["id"], name="Staff sign-in", durationDays=5)
    url = f"/api/v1/wbs/nodes/{node['id']}"

    r = wbs_client.patch(url, json={"skills": ["Okta SSO", "Security Review"]}, headers=h)
    assert r.status_code == 200, r.text
    assert [(skill["name"], skill["minProficiency"]) for skill in r.json()["skills"]] == [
        ("Okta SSO", 3),
        ("Security Review", 3),
    ]
    assert r.json()["durationDays"] == 5  # the node's own fields are untouched
    r = wbs_client.patch(url, json={"name": "Sign-in", "skills": []}, headers=h)
    assert (r.json()["name"], r.json()["skills"]) == ("Sign-in", [])
    assert wbs_client.patch(url, json={"skills": None}, headers=h).status_code == 422
    assert wbs_client.patch(url, json={"skills": [""]}, headers=h).status_code == 422


def test_move_reorders_siblings(wbs_client: TestClient, store: Store, auth: Auth) -> None:
    user, _, pid = create_project(wbs_client, store, auth)
    h = auth(user)
    root = add_node(wbs_client, pid, h, name="CRM")
    a, b, c = (add_node(wbs_client, pid, h, parentId=root["id"], name=n) for n in "ABC")

    r = wbs_client.post(f"/api/v1/wbs/nodes/{c['id']}/move", json={"position": 0}, headers=h)
    assert r.status_code == 200, r.text
    tree = r.json()
    assert [(n["code"], n["name"], n["sortOrder"]) for n in tree[1:]] == [
        ("1.1", "C", 0),
        ("1.2", "A", 1),
        ("1.3", "B", 2),
    ]
    r = wbs_client.post(f"/api/v1/wbs/nodes/{c['id']}/move", json={"position": 99}, headers=h)
    assert [n["name"] for n in r.json()[1:]] == ["A", "B", "C"]

    move_root = wbs_client.post(
        f"/api/v1/wbs/nodes/{root['id']}/move", json={"position": 0}, headers=h
    )
    assert move_root.status_code == 422
    bad = wbs_client.post(f"/api/v1/wbs/nodes/{a['id']}/move", json={"position": -1}, headers=h)
    assert bad.status_code == 422
    assert b["level"] == 2


def test_delete_cascades(wbs_client: TestClient, store: Store, auth: Auth) -> None:
    user, _, pid = create_project(wbs_client, store, auth)
    h = auth(user)
    root = add_node(wbs_client, pid, h, name="CRM")
    d = add_node(wbs_client, pid, h, parentId=root["id"], name="D")
    add_node(wbs_client, pid, h, parentId=d["id"], name="WP")
    r = wbs_client.delete(f"/api/v1/wbs/nodes/{d['id']}", headers=h)
    assert r.status_code == 204
    assert [n["name"] for n in wbs_client.get(f"/api/v1/projects/{pid}/wbs", headers=h).json()] == [
        "CRM"
    ]
    assert wbs_client.delete(f"/api/v1/wbs/nodes/{d['id']}", headers=h).status_code == 404


@pytest.mark.parametrize(
    ("position", "expected"),
    [(0, "xabc"), (1, "axbc"), (3, "abcx"), (10, "abcx")],
)
def test_reorder(position: int, expected: str) -> None:
    ids = {ch: uuid.uuid4() for ch in "abcx"}
    names = {v: k for k, v in ids.items()}
    siblings = [ids["a"], ids["x"], ids["b"], ids["c"]]
    assert "".join(names[i] for i in reorder(siblings, ids["x"], position)) == expected
