"""Writes an AI-generated WBS into a project (the WBS generate job's service connection).

Purpose: Turn a generated plan into WBS rows the PM can then edit: root, deliverables, work
packages, the skills they need and the links between them. Work packages keep
`estimate_confirmed_at` null because AI estimates count only once a person confirms them.
How it fits: Mixed into `AiJobStore` (app/repositories/ai_jobs.py), used by the background job in
app/jobs/ai_jobs.py; every query names the project because RLS does not apply; layer: repositories.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.wbs_generate import MIN_PROFICIENCY, WbsPlan
from app.domain.common import ItemOrigin
from app.repositories.tables import (
    dependencies,
    project_skills,
    projects,
    skills,
    task_skill_requirements,
    wbs_nodes,
)

ROOT_LEVEL = 1
DELIVERABLE_LEVEL = 2
WORK_PACKAGE_LEVEL = 3
MAX_NODE_NAME = 300


async def project_has_wbs(conn: AsyncConnection, project_id: uuid.UUID) -> bool:
    """Tell whether the project already has any WBS node."""
    statement = sa.select(wbs_nodes.c.id).where(wbs_nodes.c.project_id == project_id).limit(1)
    return (await conn.execute(statement)).first() is not None


def _node_row(
    project_id: uuid.UUID,
    parent: uuid.UUID | None,
    level: int,
    order: int,
    name: str,
    **extra: Any,
) -> dict[str, Any]:
    """Return the insert values of one AI-made WBS node with a fresh id."""
    return {
        "id": uuid.uuid4(),
        "project_id": project_id,
        "parent_id": parent,
        "level": level,
        "sort_order": order,
        "name": name,
        "description": extra.get("description"),
        "duration_days": extra.get("duration_days"),
        "owner_employee_id": extra.get("owner_employee_id"),
        "scope_item_id": extra.get("scope_item_id"),
        "origin": ItemOrigin.AI,
    }


class GeneratedWbsWriter:
    """Writes a generated WBS plan; mixed into `AiJobStore` (service connection)."""

    _conn: AsyncConnection

    async def has_wbs(self, project_id: uuid.UUID) -> bool:
        """Tell whether the project already has a WBS (generation never overwrites one)."""
        return await project_has_wbs(self._conn, project_id)

    async def write_plan(
        self,
        project_id: uuid.UUID,
        plan: WbsPlan,
        owners: dict[str, uuid.UUID],
    ) -> dict[str, int] | None:
        """Write the root, deliverables, work packages, skills and dependencies.

        Returns the counts, or None when the project already has a WBS (or is gone).
        """
        project = (
            await self._conn.execute(
                sa.select(projects.c.org_id, projects.c.name).where(projects.c.id == project_id)
            )
        ).first()
        if project is None or await self.has_wbs(project_id):
            return None
        root = _node_row(project_id, None, ROOT_LEVEL, 0, str(project.name)[:MAX_NODE_NAME])
        await self._conn.execute(sa.insert(wbs_nodes), [root])
        deliverable_rows: list[dict[str, Any]] = []
        package_rows: list[dict[str, Any]] = []
        package_ids: dict[str, uuid.UUID] = {}
        package_skills: list[tuple[uuid.UUID, str]] = []
        for deliverable_index, deliverable in enumerate(plan.deliverables):
            deliverable_row = _node_row(
                project_id,
                root["id"],
                DELIVERABLE_LEVEL,
                deliverable_index,
                deliverable.name,
                description=deliverable.description,
                scope_item_id=deliverable.scope_item_id,
            )
            deliverable_rows.append(deliverable_row)
            for package_index, package in enumerate(deliverable.packages):
                package_row = _node_row(
                    project_id,
                    deliverable_row["id"],
                    WORK_PACKAGE_LEVEL,
                    package_index,
                    package.name,
                    description=package.description,
                    duration_days=package.duration_days,
                    owner_employee_id=owners.get(package.key),
                    scope_item_id=package.scope_item_id,
                )
                package_rows.append(package_row)
                package_ids[package.key] = package_row["id"]
                package_skills.extend((package_row["id"], skill) for skill in package.skills)
        if deliverable_rows:
            await self._conn.execute(sa.insert(wbs_nodes), deliverable_rows)
        if package_rows:
            await self._conn.execute(sa.insert(wbs_nodes), package_rows)
        if package_skills:
            await self._write_skills(project_id, project.org_id, package_skills)
        added = await self._write_dependencies(project_id, plan, package_ids)
        return {
            "deliverables": len(deliverable_rows),
            "workPackages": len(package_rows),
            "dependencies": added,
        }

    async def _write_skills(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        package_skills: list[tuple[uuid.UUID, str]],
    ) -> None:
        """Tag the work packages with their skills and put those skills on the project's list.

        Every generated skill is required; one already on the list becomes required.
        """
        names = list({skill.casefold(): skill for _, skill in package_skills}.values())
        ids = await self._skill_ids(org_id, names)
        requirements = {
            (node_id, ids[skill.casefold()])
            for node_id, skill in package_skills
            if skill.casefold() in ids
        }
        await self._conn.execute(
            sa.insert(task_skill_requirements),
            [
                {"wbs_node_id": node_id, "skill_id": skill_id, "min_proficiency": MIN_PROFICIENCY}
                for node_id, skill_id in sorted(requirements, key=str)
            ],
        )
        skill_ids = sorted({skill_id for _, skill_id in requirements}, key=str)
        upsert = pg_insert(project_skills).values(
            [
                {"project_id": project_id, "skill_id": skill_id, "required": True}
                for skill_id in skill_ids
            ]
        )
        await self._conn.execute(
            upsert.on_conflict_do_update(
                index_elements=[project_skills.c.project_id, project_skills.c.skill_id],
                set_={"required": True},
            )
        )

    async def _write_dependencies(
        self, project_id: uuid.UUID, plan: WbsPlan, package_ids: dict[str, uuid.UUID]
    ) -> int:
        """Insert the plan's links between known work packages; return how many were kept."""
        added = 0
        for link in plan.dependencies:
            predecessor = package_ids.get(link.predecessor)
            successor = package_ids.get(link.successor)
            if predecessor is None or successor is None:
                continue
            try:
                # A savepoint per link: the cycle trigger may reject one without
                # aborting the whole WBS.
                async with self._conn.begin_nested():
                    await self._conn.execute(
                        sa.insert(dependencies).values(
                            id=uuid.uuid4(),
                            project_id=project_id,
                            predecessor_id=predecessor,
                            successor_id=successor,
                            type=link.type,
                            lag_days=link.lag_days,
                            origin=ItemOrigin.AI,
                        )
                    )
            except DBAPIError:
                continue
            added += 1
        return added

    async def _skill_ids(self, org_id: uuid.UUID, names: list[str]) -> dict[str, uuid.UUID]:
        """Return org skill ids by casefolded name, creating the missing skills (citext unique)."""
        known = await self._org_skill_ids(org_id)
        missing = [name for name in names if name.casefold() not in known]
        if not missing:
            return known
        await self._conn.execute(
            pg_insert(skills)
            .values([{"id": uuid.uuid4(), "org_id": org_id, "name": name} for name in missing])
            .on_conflict_do_nothing()
        )
        return await self._org_skill_ids(org_id)

    async def _org_skill_ids(self, org_id: uuid.UUID) -> dict[str, uuid.UUID]:
        """Return all of the organization's skill ids by casefolded name."""
        statement = sa.select(skills.c.id, skills.c.name).where(skills.c.org_id == org_id)
        return {
            str(row.name).casefold(): row.id for row in (await self._conn.execute(statement)).all()
        }
