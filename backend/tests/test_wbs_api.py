"""WBS endpoints with real JWT verification and an in-memory fake repository."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import OrgRole
from app.domain.wbs import EstimateSource, WbsNode, estimate_state, reorder
from app.repositories.access import ProjectRef
from app.repositories.wbs import NodeRef
from tests.conftest import Store

Auth = Callable[[uuid.UUID], dict[str, str]]


@dataclass
class WbsStore:
    nodes: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    employees: dict[uuid.UUID, tuple[uuid.UUID, str, bool]] = field(default_factory=dict)
    applied: dict[uuid.UUID, int] = field(default_factory=dict)
    skills: dict[uuid.UUID, list[str]] = field(default_factory=dict)  # node id -> skill names
    seq: int = 0


class FakeWbs:
    def __init__(self, store: Store, w: WbsStore, user_id: uuid.UUID) -> None:
        self.s, self.w, self.uid = store, w, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        p = self.s.projects.get(project_id)
        if p is None or self._role(project_id) is None:
            return None
        return ProjectRef(p.id, p.org_id, p.name, p.start_date, p.target_end_date, p.budget_cents)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def is_active_employee(self, org_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        e = self.w.employees.get(employee_id)
        return e is not None and e[0] == org_id and e[2]

    async def applied_version(self, project_id: uuid.UUID) -> int | None:
        return self.w.applied.get(project_id)

    async def confirm_estimates(
        self, project_id: uuid.UUID, node_ids: list[uuid.UUID] | None
    ) -> int:
        count = 0
        for n in self.w.nodes.values():
            if (
                n["project_id"] == project_id
                and n["origin"] == "ai"
                and n.get("duration_days") is not None
                and n.get("estimate_confirmed_at") is None
                and (node_ids is None or n["id"] in node_ids)
            ):
                n["estimate_confirmed_at"] = datetime.now(UTC)
                count += 1
        return count

    def _children(self, project_id: uuid.UUID, parent_id: uuid.UUID | None) -> list[uuid.UUID]:
        rows = [
            n
            for n in self.w.nodes.values()
            if n["project_id"] == project_id and n["parent_id"] == parent_id
        ]
        rows.sort(key=lambda n: (n["sort_order"], n["seq"]))
        return [n["id"] for n in rows]

    async def tree(self, project_id: uuid.UUID) -> list[WbsNode]:
        if self._role(project_id) is None:
            return []
        out: list[WbsNode] = []

        def walk(parent: uuid.UUID | None, prefix: str) -> None:
            for i, node_id in enumerate(self._children(project_id, parent), start=1):
                n = self.w.nodes[node_id]
                code = f"{prefix}.{i}" if prefix else "1"
                owner = (
                    self.w.employees.get(n["owner_employee_id"]) if n["owner_employee_id"] else None
                )
                confirmed, source = estimate_state(
                    n["origin"], n.get("duration_days"), n.get("estimate_confirmed_at")
                )
                hidden = ("project_id", "seq", "estimate_confirmed_at")
                out.append(
                    WbsNode.model_validate(
                        {
                            **{k: v for k, v in n.items() if k not in hidden},
                            "code": code,
                            "owner_name": owner[1] if owner else None,
                            "skills": [
                                {
                                    "skill_id": uuid.uuid5(uuid.NAMESPACE_OID, name.lower()),
                                    "name": name,
                                    "min_proficiency": 3,
                                }
                                for name in self.w.skills.get(node_id, [])
                            ],
                            "estimate_confirmed": confirmed,
                            "estimate_source": source,
                        }
                    )
                )
                walk(node_id, code)

        walk(None, "")
        return out

    async def get(self, node_id: uuid.UUID) -> WbsNode | None:
        n = self.w.nodes.get(node_id)
        if n is None:
            return None
        return next((x for x in await self.tree(n["project_id"]) if x.id == node_id), None)

    async def node(self, node_id: uuid.UUID) -> NodeRef | None:
        n = self.w.nodes.get(node_id)
        if n is None or self._role(n["project_id"]) is None:
            return None
        return NodeRef(n["id"], n["project_id"], n["parent_id"], n["level"])

    async def has_root(self, project_id: uuid.UUID) -> bool:
        return bool(self._children(project_id, None))

    async def sibling_ids(
        self, project_id: uuid.UUID, parent_id: uuid.UUID | None
    ) -> list[uuid.UUID]:
        return self._children(project_id, parent_id)

    async def create(
        self,
        project_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        level: int,
        values: dict[str, Any],
    ) -> uuid.UUID:
        siblings = self._children(project_id, parent_id)
        order = max((self.w.nodes[s]["sort_order"] for s in siblings), default=-1) + 1
        node_id = uuid.uuid4()
        self.w.seq += 1
        self.w.nodes[node_id] = {
            "id": node_id,
            "project_id": project_id,
            "parent_id": parent_id,
            "level": level,
            "sort_order": order,
            "effort_hours": None,
            "origin": "manual",
            "scope_item_id": None,
            "seq": self.w.seq,
            **values,
        }
        return node_id

    async def update(self, node_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        self.w.nodes[node_id].update(changes)
        if "duration_days" in changes:
            entered = changes["duration_days"] is not None
            self.w.nodes[node_id]["estimate_confirmed_at"] = datetime.now(UTC) if entered else None
        return True

    async def set_skills(self, org_id: uuid.UUID, node_id: uuid.UUID, names: list[str]) -> None:
        self.w.skills[node_id] = list(dict.fromkeys(names))

    async def set_order(self, ordered_ids: list[uuid.UUID]) -> None:
        for i, node_id in enumerate(ordered_ids):
            self.w.nodes[node_id]["sort_order"] = i

    async def delete(self, node_id: uuid.UUID) -> bool:
        doomed = [node_id]
        while doomed:
            current = doomed.pop()
            doomed += [n["id"] for n in self.w.nodes.values() if n["parent_id"] == current]
            self.w.nodes.pop(current, None)
        return True


@pytest.fixture
def wbs_store() -> WbsStore:
    return WbsStore()


@pytest.fixture
def wbs_client(app: FastAPI, store: Store, wbs_store: WbsStore) -> Iterator[TestClient]:
    def repo(principal: CurrentPrincipal) -> FakeWbs:
        return FakeWbs(store, wbs_store, principal.user_id)

    app.dependency_overrides[deps.get_wbs_repo] = repo
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID, str]:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    r = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "CRM"}, headers=auth(user))
    return user, org, r.json()["id"]


def _add(client: TestClient, pid: str, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    r = client.post(f"/api/v1/projects/{pid}/wbs/nodes", json=body, headers=headers)
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


NODE_KEYS = {
    "id",
    "parentId",
    "level",
    "sortOrder",
    "code",
    "name",
    "description",
    "durationDays",
    "effortHours",
    "ownerEmployeeId",
    "ownerName",
    "origin",
    "scopeItemId",
    "skills",
    "estimateConfirmed",
    "estimateSource",
}


def test_create_tree_and_list_in_order(
    wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth
) -> None:
    user, org, pid = _project(wbs_client, store, auth)
    h = auth(user)
    assert wbs_client.get(f"/api/v1/projects/{pid}/wbs", headers=h).json() == []

    emp = uuid.uuid4()
    wbs_store.employees[emp] = (org, "Mark Chen", True)
    root = _add(wbs_client, pid, h, parentId=None, name="CRM")
    assert set(root) == NODE_KEYS
    assert (root["level"], root["code"], root["sortOrder"]) == (1, "1", 0)
    d1 = _add(wbs_client, pid, h, parentId=root["id"], name="Design")
    d2 = _add(wbs_client, pid, h, parentId=root["id"], name="Build")
    wp = _add(
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
    user, _, pid = _project(wbs_client, store, auth)
    h = auth(user)
    url = f"/api/v1/projects/{pid}/wbs/nodes"
    root = _add(wbs_client, pid, h, name="CRM")
    assert wbs_client.post(url, json={"name": "Second root"}, headers=h).status_code == 409

    d = _add(wbs_client, pid, h, parentId=root["id"], name="D")
    wp = _add(wbs_client, pid, h, parentId=d["id"], name="WP")
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
    owner, org, pid = _project(wbs_client, store, auth)
    root = _add(wbs_client, pid, auth(owner), name="CRM")
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
    user, org, pid = _project(wbs_client, store, auth)
    h = auth(user)
    emp, inactive = uuid.uuid4(), uuid.uuid4()
    wbs_store.employees[emp] = (org, "Mark Chen", True)
    wbs_store.employees[inactive] = (org, "Gone", False)
    root = _add(wbs_client, pid, h, name="CRM")
    node = _add(wbs_client, pid, h, parentId=root["id"], name="Design", durationDays=5)
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
    user, _, pid = _project(wbs_client, store, auth)
    h = auth(user)
    root = _add(wbs_client, pid, h, name="CRM")
    node = _add(wbs_client, pid, h, parentId=root["id"], name="Staff sign-in", durationDays=5)
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
    user, _, pid = _project(wbs_client, store, auth)
    h = auth(user)
    root = _add(wbs_client, pid, h, name="CRM")
    a, b, c = (_add(wbs_client, pid, h, parentId=root["id"], name=n) for n in "ABC")

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
    user, _, pid = _project(wbs_client, store, auth)
    h = auth(user)
    root = _add(wbs_client, pid, h, name="CRM")
    d = _add(wbs_client, pid, h, parentId=root["id"], name="D")
    _add(wbs_client, pid, h, parentId=d["id"], name="WP")
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


def _ai_node(w: WbsStore, pid: str, parent: str, name: str, days: int | None) -> uuid.UUID:
    node_id = uuid.uuid4()
    w.seq += 1
    w.nodes[node_id] = {
        "id": node_id,
        "project_id": uuid.UUID(pid),
        "parent_id": uuid.UUID(parent),
        "level": 3,
        "sort_order": w.seq,
        "name": name,
        "description": None,
        "duration_days": days,
        "effort_hours": None,
        "owner_employee_id": None,
        "origin": "ai",
        "scope_item_id": None,
        "seq": w.seq,
    }
    return node_id


def test_confirm_estimates(
    wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth
) -> None:
    user, org, pid = _project(wbs_client, store, auth)
    h = auth(user)
    root = _add(wbs_client, pid, h, name="CRM")
    deliverable = _add(wbs_client, pid, h, parentId=root["id"], name="Build")
    first = _ai_node(wbs_store, pid, deliverable["id"], "API", 10)
    second = _ai_node(wbs_store, pid, deliverable["id"], "UI", 15)
    _ai_node(wbs_store, pid, deliverable["id"], "Docs", None)
    mine = _add(wbs_client, pid, h, parentId=deliverable["id"], name="QA", durationDays=5)
    assert (mine["estimateConfirmed"], mine["estimateSource"]) == (True, "yours")

    def states() -> dict[str, tuple[bool, str | None]]:
        tree = wbs_client.get(f"/api/v1/projects/{pid}/wbs", headers=h).json()
        return {n["name"]: (n["estimateConfirmed"], n["estimateSource"]) for n in tree}

    assert states()["API"] == (False, "ai")
    url = f"/api/v1/projects/{pid}/wbs/confirm-estimates"
    r = wbs_client.post(url, json={"nodeIds": [str(first)]}, headers=h)
    assert r.status_code == 200, r.text
    assert (states()["API"], states()["UI"]) == ((True, "ai"), (False, "ai"))

    # Typing a new duration confirms it; clearing it leaves nothing to confirm.
    wbs_client.patch(f"/api/v1/wbs/nodes/{second}", json={"durationDays": 20}, headers=h)
    assert states()["UI"] == (True, "ai")
    assert wbs_client.post(url, headers=h).status_code == 200
    assert states()["Docs"] == (False, None)

    viewer = store.add_user("Vic")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert wbs_client.post(url, json={}, headers=auth(viewer)).status_code == 403
    missing = f"/api/v1/projects/{uuid.uuid4()}/wbs/confirm-estimates"
    assert wbs_client.post(missing, json={}, headers=h).status_code == 404


def test_inputs_lock_once_a_plan_is_applied(
    wbs_client: TestClient, store: Store, wbs_store: WbsStore, auth: Auth
) -> None:
    user, _, pid = _project(wbs_client, store, auth)
    h = auth(user)
    root = _add(wbs_client, pid, h, name="CRM")
    wbs_store.applied[uuid.UUID(pid)] = 2
    r = wbs_client.patch(f"/api/v1/wbs/nodes/{root['id']}", json={"name": "X"}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"] == "Plan v2 is in force; record a change on step 7"
    assert r.json()["version"] == 2
    r = wbs_client.post(f"/api/v1/projects/{pid}/wbs/nodes", json={"name": "Y"}, headers=h)
    assert r.status_code == 409
    url = f"/api/v1/projects/{pid}/wbs/confirm-estimates"
    assert wbs_client.post(url, json={}, headers=h).status_code == 409
    assert wbs_client.delete(f"/api/v1/wbs/nodes/{root['id']}", headers=h).status_code == 409
    assert wbs_client.get(f"/api/v1/projects/{pid}/wbs", headers=h).status_code == 200


@pytest.mark.parametrize(
    ("origin", "days", "confirmed_at", "expected"),
    [
        ("ai", 10, None, (False, EstimateSource.AI)),
        ("ai", 10, datetime(2026, 11, 13, tzinfo=UTC), (True, EstimateSource.AI)),
        ("manual", 10, None, (True, EstimateSource.YOURS)),
        ("manual", None, None, (False, None)),
        ("ai", None, None, (False, None)),
    ],
)
def test_estimate_state(
    origin: str,
    days: int | None,
    confirmed_at: datetime | None,
    expected: tuple[bool, EstimateSource | None],
) -> None:
    assert estimate_state(origin, days, confirmed_at) == expected
