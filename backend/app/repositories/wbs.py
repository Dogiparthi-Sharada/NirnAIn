"""Repository of WBS nodes: the tree, edits, ordering, needed skills and estimate confirmation.

Purpose: Persist the work breakdown structure the PM builds on step 4; reads use the
`wbs_nodes_with_code` view so codes (1.2.3) always follow the tree position.
How it fits: Used by app/api/v1/wbs.py; the built-in checks' input comes from
app/repositories/wbs_checks.py; layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.wbs_validate import ValidationInput
from app.domain.common import ItemOrigin
from app.domain.wbs import WbsNode, WbsSkill, estimate_state
from app.optimization.skill_links import LINKED_PROFICIENCY
from app.repositories import access
from app.repositories.access import ProjectRef
from app.repositories.tables import (
    employees,
    skills,
    task_skill_requirements,
    wbs_nodes,
    wbs_nodes_with_code,
)
from app.repositories.wbs_checks import read_validation_input, wbs_changed_at


@dataclass(frozen=True, slots=True)
class NodeRef:
    """The fields of a WBS node needed to check a move or an edit."""

    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    level: int


def _node_select() -> sa.Select[Any]:
    """Return the node select with the owner's name and the estimate confirmation joined in."""
    view, owners, nodes = wbs_nodes_with_code, employees, wbs_nodes
    return sa.select(
        view.c.id,
        view.c.parent_id,
        view.c.level,
        view.c.sort_order,
        view.c.code,
        view.c.name,
        view.c.description,
        view.c.duration_days,
        view.c.effort_hours,
        view.c.owner_employee_id,
        owners.c.full_name.label("owner_name"),
        view.c.origin,
        view.c.scope_item_id,
        nodes.c.estimate_confirmed_at,
    ).select_from(
        view.join(nodes, nodes.c.id == view.c.id).outerjoin(
            owners, owners.c.id == view.c.owner_employee_id
        )
    )


class WbsRepository:
    """WBS persistence. Visibility and write permissions come from RLS."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        """Return the project when the caller can see it, else None."""
        return await access.visible_project(self._conn, project_id)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        """Tell whether the caller may change the project."""
        return await access.can_write_project(self._conn, project_id)

    async def is_active_employee(self, org_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        """Tell whether the employee is active in the organization (a valid owner)."""
        return await access.is_active_employee(self._conn, org_id, employee_id)

    async def check_input(self, project_id: uuid.UUID, today: date) -> ValidationInput | None:
        """What the built-in checks read, as the caller sees it (None: project not visible)."""
        return await read_validation_input(self._conn, project_id, today)

    async def changed_at(self, project_id: uuid.UUID) -> datetime | None:
        """When the project's WBS nodes last changed."""
        return await wbs_changed_at(self._conn, project_id)

    async def tree(self, project_id: uuid.UUID) -> list[WbsNode]:
        """Return the project's whole WBS in tree order, with skills and estimate state."""
        view = wbs_nodes_with_code
        stmt = _node_select().where(view.c.project_id == project_id).order_by(view.c.path)
        return await self._with_skills((await self._conn.execute(stmt)).all())

    async def get(self, node_id: uuid.UUID) -> WbsNode | None:
        """Return one node with its skills and estimate state, or None."""
        view = wbs_nodes_with_code
        node = await self.node(node_id)
        if node is None:
            return None
        stmt = _node_select().where(view.c.project_id == node.project_id, view.c.id == node_id)
        found = await self._with_skills((await self._conn.execute(stmt)).all())
        return found[0] if found else None

    async def _with_skills(self, rows: Any) -> list[WbsNode]:
        """Turn node rows into API models with their needed skills and estimate state."""
        ids = [row.id for row in rows]
        by_node: dict[uuid.UUID, list[WbsSkill]] = defaultdict(list)
        if ids:
            requirements, skill_table = task_skill_requirements, skills
            for requirement in (
                await self._conn.execute(
                    sa.select(
                        requirements.c.wbs_node_id,
                        requirements.c.skill_id,
                        skill_table.c.name,
                        requirements.c.min_proficiency,
                    )
                    .select_from(
                        requirements.join(skill_table, skill_table.c.id == requirements.c.skill_id)
                    )
                    .where(requirements.c.wbs_node_id.in_(ids))
                    .order_by(requirements.c.wbs_node_id, skill_table.c.name)
                )
            ).all():
                by_node[requirement.wbs_node_id].append(
                    WbsSkill(
                        skill_id=requirement.skill_id,
                        name=requirement.name,
                        min_proficiency=requirement.min_proficiency,
                    )
                )
        out = []
        for row in rows:
            confirmed, source = estimate_state(
                row.origin, row.duration_days, row.estimate_confirmed_at
            )
            out.append(
                WbsNode.model_validate(
                    {
                        **row._mapping,
                        "skills": by_node.get(row.id, []),
                        "estimate_confirmed": confirmed,
                        "estimate_source": source,
                    }
                )
            )
        return out

    async def node(self, node_id: uuid.UUID) -> NodeRef | None:
        """Return the node's project, parent and level, or None when missing or hidden."""
        nodes = wbs_nodes
        row = (
            await self._conn.execute(
                sa.select(nodes.c.id, nodes.c.project_id, nodes.c.parent_id, nodes.c.level).where(
                    nodes.c.id == node_id
                )
            )
        ).first()
        return NodeRef(**row._mapping) if row else None

    async def has_root(self, project_id: uuid.UUID) -> bool:
        """Tell whether the project already has a level-1 root node."""
        nodes = wbs_nodes
        stmt = (
            sa.select(nodes.c.id)
            .where(nodes.c.project_id == project_id, nodes.c.level == 1)
            .limit(1)
        )
        return (await self._conn.execute(stmt)).first() is not None

    async def sibling_ids(
        self, project_id: uuid.UUID, parent_id: uuid.UUID | None
    ) -> list[uuid.UUID]:
        """Siblings in display order (the view numbers them the same way)."""
        nodes = wbs_nodes
        parent = (
            nodes.c.parent_id.is_(None) if parent_id is None else nodes.c.parent_id == parent_id
        )
        stmt = (
            sa.select(nodes.c.id)
            .where(nodes.c.project_id == project_id, parent)
            .order_by(nodes.c.sort_order, nodes.c.created_at, nodes.c.id)
        )
        return list((await self._conn.execute(stmt)).scalars())

    async def create(
        self,
        project_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        level: int,
        values: dict[str, Any],
    ) -> uuid.UUID:
        """Insert a node after its existing siblings and return its id."""
        # Id generated here and the row re-selected (RETURNING visibility depends
        # on SELECT policies). New nodes go after their existing siblings.
        nodes = wbs_nodes
        parent = (
            nodes.c.parent_id.is_(None) if parent_id is None else nodes.c.parent_id == parent_id
        )
        next_order = (
            sa.select(sa.func.coalesce(sa.func.max(nodes.c.sort_order) + 1, 0))
            .where(nodes.c.project_id == project_id, parent)
            .scalar_subquery()
        )
        node_id = uuid.uuid4()
        await self._conn.execute(
            sa.insert(nodes).values(
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
        """Update the node's columns; a duration typed in by a person counts as confirmed.

        Clearing the duration clears the confirmation. Returns False when nothing was updated.
        """
        values = dict(changes)
        if "duration_days" in values:
            # A duration a person types in is their estimate: confirmed by entering it.
            entered = values["duration_days"] is not None
            values["estimate_confirmed_at"] = sa.func.now() if entered else None
            values["estimate_confirmed_by"] = sa.func.auth.uid() if entered else None
        stmt = sa.update(wbs_nodes).where(wbs_nodes.c.id == node_id).values(**values)
        return (await self._conn.execute(stmt)).rowcount > 0

    async def set_skills(self, org_id: uuid.UUID, node_id: uuid.UUID, names: list[str]) -> None:
        """Replace the skills the node needs, by name (the organization's skills; new
        names are added to it). New ones are needed at proficiency 3, kept ones keep
        their level."""
        skill_ids = await self._skill_ids(org_id, names)
        required = task_skill_requirements
        stale = sa.delete(required).where(required.c.wbs_node_id == node_id)
        if skill_ids:
            stale = stale.where(required.c.skill_id.not_in(skill_ids))
        await self._conn.execute(stale)
        if not skill_ids:
            return
        await self._conn.execute(
            pg_insert(required)
            .values(
                [
                    {
                        "wbs_node_id": node_id,
                        "skill_id": skill_id,
                        "min_proficiency": LINKED_PROFICIENCY,
                    }
                    for skill_id in skill_ids
                ]
            )
            .on_conflict_do_nothing(index_elements=[required.c.wbs_node_id, required.c.skill_id])
        )

    async def _skill_ids(self, org_id: uuid.UUID, names: list[str]) -> list[uuid.UUID]:
        """Ids of the organization's skills with these names (case-insensitive), adding
        the missing ones; only then is the managers-only insert needed."""
        wanted = list(dict.fromkeys(name.lower() for name in names))
        known = await self._known_skills(org_id, wanted)
        missing = [name for name in names if name.lower() not in known]
        if missing:
            await self._conn.execute(
                pg_insert(skills)
                .values([{"id": uuid.uuid4(), "org_id": org_id, "name": name} for name in missing])
                .on_conflict_do_nothing(index_elements=[skills.c.org_id, skills.c.name])
            )
            known = await self._known_skills(org_id, wanted)
        return [known[name] for name in wanted if name in known]

    async def _known_skills(self, org_id: uuid.UUID, lowered: list[str]) -> dict[str, uuid.UUID]:
        """Return the organization's skill ids by lower-cased name, for the given names."""
        if not lowered:
            return {}
        rows = (
            await self._conn.execute(
                sa.select(skills.c.id, skills.c.name).where(
                    skills.c.org_id == org_id, sa.func.lower(skills.c.name).in_(lowered)
                )
            )
        ).all()
        return {str(row.name).lower(): row.id for row in rows}

    async def confirm_estimates(
        self, project_id: uuid.UUID, node_ids: list[uuid.UUID] | None
    ) -> int:
        """Confirm the project's unconfirmed AI estimates (only `node_ids` when given)."""
        nodes = wbs_nodes
        stmt = sa.update(nodes).where(
            nodes.c.project_id == project_id,
            nodes.c.origin == ItemOrigin.AI,
            nodes.c.duration_days.is_not(None),
            nodes.c.estimate_confirmed_at.is_(None),
        )
        if node_ids is not None:
            stmt = stmt.where(nodes.c.id.in_(node_ids))
        result = await self._conn.execute(
            stmt.values(
                estimate_confirmed_at=sa.func.now(), estimate_confirmed_by=sa.func.auth.uid()
            )
        )
        return result.rowcount

    async def set_order(self, ordered_ids: list[uuid.UUID]) -> None:
        """Store the siblings' new order, touching only rows whose position changed."""
        nodes = wbs_nodes
        for index, node_id in enumerate(ordered_ids):
            await self._conn.execute(
                sa.update(nodes)
                .where(nodes.c.id == node_id, nodes.c.sort_order != index)
                .values(sort_order=index)
            )

    async def delete(self, node_id: uuid.UUID) -> bool:
        """Delete the node (its children go with it); False when nothing was deleted."""
        result = await self._conn.execute(sa.delete(wbs_nodes).where(wbs_nodes.c.id == node_id))
        return result.rowcount > 0
