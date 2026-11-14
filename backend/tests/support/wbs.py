"""Moved test support."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from app.domain.wbs import WbsNode, estimate_state
from app.repositories.access import ProjectRef
from app.repositories.wbs import NodeRef
from tests.support.store import ProjectScopedFake, Store


@dataclass
class WbsStore:
    nodes: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    employees: dict[uuid.UUID, tuple[uuid.UUID, str, bool]] = field(default_factory=dict)
    skills: dict[uuid.UUID, list[str]] = field(default_factory=dict)  # node id -> skill names
    seq: int = 0


class FakeWbs(ProjectScopedFake):
    def __init__(self, store: Store, w: WbsStore, user_id: uuid.UUID) -> None:
        super().__init__(store, user_id)
        self.wbs_data = w

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        p = self.store.projects.get(project_id)
        if p is None or self._role(project_id) is None:
            return None
        return ProjectRef(p.id, p.org_id, p.name, p.start_date, p.target_end_date, p.budget_cents)

    async def is_active_employee(self, org_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        e = self.wbs_data.employees.get(employee_id)
        return e is not None and e[0] == org_id and e[2]

    async def confirm_estimates(
        self, project_id: uuid.UUID, node_ids: list[uuid.UUID] | None
    ) -> int:
        count = 0
        for n in self.wbs_data.nodes.values():
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
            for n in self.wbs_data.nodes.values()
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
                n = self.wbs_data.nodes[node_id]
                code = f"{prefix}.{i}" if prefix else "1"
                owner = (
                    self.wbs_data.employees.get(n["owner_employee_id"])
                    if n["owner_employee_id"]
                    else None
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
                                for name in self.wbs_data.skills.get(node_id, [])
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
        n = self.wbs_data.nodes.get(node_id)
        if n is None:
            return None
        return next((x for x in await self.tree(n["project_id"]) if x.id == node_id), None)

    async def node(self, node_id: uuid.UUID) -> NodeRef | None:
        n = self.wbs_data.nodes.get(node_id)
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
        order = max((self.wbs_data.nodes[s]["sort_order"] for s in siblings), default=-1) + 1
        node_id = uuid.uuid4()
        self.wbs_data.seq += 1
        self.wbs_data.nodes[node_id] = {
            "id": node_id,
            "project_id": project_id,
            "parent_id": parent_id,
            "level": level,
            "sort_order": order,
            "effort_hours": None,
            "origin": "manual",
            "scope_item_id": None,
            "seq": self.wbs_data.seq,
            **values,
        }
        return node_id

    async def update(self, node_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        self.wbs_data.nodes[node_id].update(changes)
        if "duration_days" in changes:
            entered = changes["duration_days"] is not None
            self.wbs_data.nodes[node_id]["estimate_confirmed_at"] = (
                datetime.now(UTC) if entered else None
            )
        return True

    async def set_skills(self, org_id: uuid.UUID, node_id: uuid.UUID, names: list[str]) -> None:
        self.wbs_data.skills[node_id] = list(dict.fromkeys(names))

    async def set_order(self, ordered_ids: list[uuid.UUID]) -> None:
        for i, node_id in enumerate(ordered_ids):
            self.wbs_data.nodes[node_id]["sort_order"] = i

    async def delete(self, node_id: uuid.UUID) -> bool:
        doomed = [node_id]
        while doomed:
            current = doomed.pop()
            doomed += [n["id"] for n in self.wbs_data.nodes.values() if n["parent_id"] == current]
            self.wbs_data.nodes.pop(current, None)
        return True


def add_node(client: TestClient, pid: str, headers: dict[str, str], **body: Any) -> dict[str, Any]:
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
