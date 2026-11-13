"""WBS nodes. Reads use the `wbs_nodes_with_code` view (codes from tree position)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.wbs import WbsNode, WbsSkill
from app.repositories import access
from app.repositories.access import ProjectRef
from app.repositories.tables import (
    employees,
    skills,
    task_skill_requirements,
    wbs_nodes,
    wbs_nodes_with_code,
)


@dataclass(frozen=True, slots=True)
class NodeRef:
    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    level: int


def _node_select() -> sa.Select[Any]:
    w, e = wbs_nodes_with_code, employees
    return sa.select(
        w.c.id,
        w.c.parent_id,
        w.c.level,
        w.c.sort_order,
        w.c.code,
        w.c.name,
        w.c.description,
        w.c.duration_days,
        w.c.effort_hours,
        w.c.owner_employee_id,
        e.c.full_name.label("owner_name"),
        w.c.origin,
        w.c.scope_item_id,
    ).select_from(w.outerjoin(e, e.c.id == w.c.owner_employee_id))


class WbsRepository:
    """WBS persistence. Visibility and write permissions come from RLS."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        return await access.visible_project(self._conn, project_id)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return await access.can_write_project(self._conn, project_id)

    async def is_active_employee(self, org_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        return await access.is_active_employee(self._conn, org_id, employee_id)

    async def tree(self, project_id: uuid.UUID) -> list[WbsNode]:
        w = wbs_nodes_with_code
        stmt = _node_select().where(w.c.project_id == project_id).order_by(w.c.path)
        return await self._with_skills((await self._conn.execute(stmt)).all())

    async def get(self, node_id: uuid.UUID) -> WbsNode | None:
        w = wbs_nodes_with_code
        node = await self.node(node_id)
        if node is None:
            return None
        stmt = _node_select().where(w.c.project_id == node.project_id, w.c.id == node_id)
        found = await self._with_skills((await self._conn.execute(stmt)).all())
        return found[0] if found else None

    async def _with_skills(self, rows: Any) -> list[WbsNode]:
        ids = [row.id for row in rows]
        by_node: dict[uuid.UUID, list[WbsSkill]] = defaultdict(list)
        if ids:
            t, s = task_skill_requirements, skills
            for req in (
                await self._conn.execute(
                    sa.select(t.c.wbs_node_id, t.c.skill_id, s.c.name, t.c.min_proficiency)
                    .select_from(t.join(s, s.c.id == t.c.skill_id))
                    .where(t.c.wbs_node_id.in_(ids))
                    .order_by(t.c.wbs_node_id, s.c.name)
                )
            ).all():
                by_node[req.wbs_node_id].append(
                    WbsSkill(
                        skill_id=req.skill_id, name=req.name, min_proficiency=req.min_proficiency
                    )
                )
        return [
            WbsNode.model_validate({**row._mapping, "skills": by_node.get(row.id, [])})
            for row in rows
        ]

    async def node(self, node_id: uuid.UUID) -> NodeRef | None:
        n = wbs_nodes
        row = (
            await self._conn.execute(
                sa.select(n.c.id, n.c.project_id, n.c.parent_id, n.c.level).where(n.c.id == node_id)
            )
        ).first()
        return NodeRef(**row._mapping) if row else None

    async def has_root(self, project_id: uuid.UUID) -> bool:
        n = wbs_nodes
        stmt = sa.select(n.c.id).where(n.c.project_id == project_id, n.c.level == 1).limit(1)
        return (await self._conn.execute(stmt)).first() is not None

    async def sibling_ids(
        self, project_id: uuid.UUID, parent_id: uuid.UUID | None
    ) -> list[uuid.UUID]:
        """Siblings in display order (the view numbers them the same way)."""
        n = wbs_nodes
        parent = n.c.parent_id.is_(None) if parent_id is None else n.c.parent_id == parent_id
        stmt = (
            sa.select(n.c.id)
            .where(n.c.project_id == project_id, parent)
            .order_by(n.c.sort_order, n.c.created_at, n.c.id)
        )
        return list((await self._conn.execute(stmt)).scalars())

    async def create(
        self,
        project_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        level: int,
        values: dict[str, Any],
    ) -> uuid.UUID:
        # Id generated here and the row re-selected (RETURNING visibility depends
        # on SELECT policies). New nodes go after their existing siblings.
        n = wbs_nodes
        parent = n.c.parent_id.is_(None) if parent_id is None else n.c.parent_id == parent_id
        next_order = (
            sa.select(sa.func.coalesce(sa.func.max(n.c.sort_order) + 1, 0))
            .where(n.c.project_id == project_id, parent)
            .scalar_subquery()
        )
        node_id = uuid.uuid4()
        await self._conn.execute(
            sa.insert(n).values(
                id=node_id,
                project_id=project_id,
                parent_id=parent_id,
                level=level,
                sort_order=next_order,
                **values,
            )
        )
        return node_id

    async def update(self, node_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        stmt = sa.update(wbs_nodes).where(wbs_nodes.c.id == node_id).values(**changes)
        return (await self._conn.execute(stmt)).rowcount > 0

    async def set_order(self, ordered_ids: list[uuid.UUID]) -> None:
        n = wbs_nodes
        for index, node_id in enumerate(ordered_ids):
            await self._conn.execute(
                sa.update(n)
                .where(n.c.id == node_id, n.c.sort_order != index)
                .values(sort_order=index)
            )

    async def delete(self, node_id: uuid.UUID) -> bool:
        result = await self._conn.execute(sa.delete(wbs_nodes).where(wbs_nodes.c.id == node_id))
        return result.rowcount > 0
