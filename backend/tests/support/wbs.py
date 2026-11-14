"""Fake WBS repository, a node-adding helper and the WBS node JSON keys.

Purpose: Keep WBS nodes in memory and derive their codes from tree position like the
`wbs_nodes_with_code` view, so the WBS endpoints can be tested without a database.
How it fits: The `wbs_client` fixture (tests/wbs_api/conftest.py) installs `FakeWbs` in place of
`WbsRepository`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from app.domain.wbs import WbsNode, estimate_state
from app.repositories.wbs import NodeRef
from tests.support.store import ProjectScopedFake, Store


@dataclass
class WbsStore:
    """What the fake WBS tables hold (nodes, employees, node skills, insertion order)."""

    nodes: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    employees: dict[uuid.UUID, tuple[uuid.UUID, str, bool]] = field(default_factory=dict)
    skills: dict[uuid.UUID, list[str]] = field(default_factory=dict)  # node id -> skill names
    seq: int = 0


class FakeWbs(ProjectScopedFake):
    """Stand-in for `WbsRepository` over the WBS data."""

    def __init__(self, store: Store, wbs_data: WbsStore, user_id: uuid.UUID) -> None:
        """Act as the given user over the store and the WBS data."""
        super().__init__(store, user_id)
        self.wbs_data = wbs_data

    async def is_active_employee(self, org_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        """Tell whether the employee is active in the organization."""
        employee = self.wbs_data.employees.get(employee_id)
        return employee is not None and employee[0] == org_id and employee[2]

    async def confirm_estimates(
        self, project_id: uuid.UUID, node_ids: list[uuid.UUID] | None
    ) -> int:
        """Confirm unconfirmed AI estimates (only `node_ids` when given); return the count."""
        count = 0
        for node in self.wbs_data.nodes.values():
            if (
                node["project_id"] == project_id
                and node["origin"] == "ai"
                and node.get("duration_days") is not None
                and node.get("estimate_confirmed_at") is None
                and (node_ids is None or node["id"] in node_ids)
            ):
                node["estimate_confirmed_at"] = datetime.now(UTC)
                count += 1
        return count

    def _children(self, project_id: uuid.UUID, parent_id: uuid.UUID | None) -> list[uuid.UUID]:
        """Return a node's children in display order."""
        rows = [
            node
            for node in self.wbs_data.nodes.values()
            if node["project_id"] == project_id and node["parent_id"] == parent_id
        ]
        rows.sort(key=lambda node: (node["sort_order"], node["seq"]))
        return [node["id"] for node in rows]

    async def tree(self, project_id: uuid.UUID) -> list[WbsNode]:
        """Return the project's WBS in tree order with codes, owners, skills and estimate state."""
        if self._role(project_id) is None:
            return []
        out: list[WbsNode] = []

        def walk(parent: uuid.UUID | None, prefix: str) -> None:
            """Append the children of `parent`, numbered under `prefix`, and recurse into them."""
            for position, node_id in enumerate(self._children(project_id, parent), start=1):
                node = self.wbs_data.nodes[node_id]
                code = f"{prefix}.{position}" if prefix else "1"
                owner = (
                    self.wbs_data.employees.get(node["owner_employee_id"])
                    if node["owner_employee_id"]
                    else None
                )
                confirmed, source = estimate_state(
                    node["origin"], node.get("duration_days"), node.get("estimate_confirmed_at")
                )
                hidden = ("project_id", "seq", "estimate_confirmed_at")
                out.append(
                    WbsNode.model_validate(
                        {
                            **{
                                column: value
                                for column, value in node.items()
                                if column not in hidden
                            },
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
        """Return one node as the tree shows it, or None."""
        node = self.wbs_data.nodes.get(node_id)
        if node is None:
            return None
        return next(
            (
                candidate
                for candidate in await self.tree(node["project_id"])
                if candidate.id == node_id
            ),
            None,
        )

    async def node(self, node_id: uuid.UUID) -> NodeRef | None:
        """Return a visible node's project, parent and level, or None."""
        stored = self.wbs_data.nodes.get(node_id)
        if stored is None or self._role(stored["project_id"]) is None:
            return None
        return NodeRef(stored["id"], stored["project_id"], stored["parent_id"], stored["level"])

    async def has_root(self, project_id: uuid.UUID) -> bool:
        """Tell whether the project has a root node."""
        return bool(self._children(project_id, None))

    async def sibling_ids(
        self, project_id: uuid.UUID, parent_id: uuid.UUID | None
    ) -> list[uuid.UUID]:
        """Return the node's siblings in display order."""
        return self._children(project_id, parent_id)

    async def create(
        self,
        project_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        level: int,
        values: dict[str, Any],
    ) -> uuid.UUID:
        """Add a node after its siblings and return its id."""
        siblings = self._children(project_id, parent_id)
        order = (
            max(
                (self.wbs_data.nodes[sibling_id]["sort_order"] for sibling_id in siblings),
                default=-1,
            )
            + 1
        )
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
        """Apply changes; a duration typed in counts as confirmed."""
        self.wbs_data.nodes[node_id].update(changes)
        if "duration_days" in changes:
            entered = changes["duration_days"] is not None
            self.wbs_data.nodes[node_id]["estimate_confirmed_at"] = (
                datetime.now(UTC) if entered else None
            )
        return True

    async def set_skills(self, org_id: uuid.UUID, node_id: uuid.UUID, names: list[str]) -> None:
        """Replace the skills the node needs."""
        self.wbs_data.skills[node_id] = list(dict.fromkeys(names))

    async def set_order(self, ordered_ids: list[uuid.UUID]) -> None:
        """Store the siblings' new order."""
        for position, node_id in enumerate(ordered_ids):
            self.wbs_data.nodes[node_id]["sort_order"] = position

    async def delete(self, node_id: uuid.UUID) -> bool:
        """Delete the node and everything under it."""
        doomed = [node_id]
        while doomed:
            current = doomed.pop()
            doomed += [
                node["id"] for node in self.wbs_data.nodes.values() if node["parent_id"] == current
            ]
            self.wbs_data.nodes.pop(current, None)
        return True


def add_node(
    client: TestClient, project_id: str, headers: dict[str, str], **body: Any
) -> dict[str, Any]:
    """Add a node through the API (expecting 201) and return its JSON."""
    response = client.post(f"/api/v1/projects/{project_id}/wbs/nodes", json=body, headers=headers)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
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
