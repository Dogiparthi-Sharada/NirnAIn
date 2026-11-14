"""Skills of a project's team: the skills the project needs and the skills its people hold.

Purpose: Keep the project's needed-skill list (derived from the WBS until the PM edits it) and each
person's skills in step, so coverage and the optimizer's skill matching see the same data.
How it fits: Mixed into `TeamRepository` (app/repositories/team.py) and used through it by
app/api/v1/team.py and the step-7 change repositories; layer: repositories.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.team import ProjectSkill
from app.repositories.tables import (
    employee_skills,
    employees,
    project_skills,
    project_team_members,
    skills,
    task_skill_requirements,
    wbs_nodes,
)

DEFAULT_PROFICIENCY = 3


@dataclass(frozen=True, slots=True)
class SkillRow:
    """A skill on the project's list and whether it is required."""

    skill_id: uuid.UUID
    name: str
    required: bool


class TeamSkillQueries:
    """Skill reads and writes, mixed into `TeamRepository` (caller's RLS session)."""

    _conn: AsyncConnection

    async def ensure_skills(self, org_id: uuid.UUID, names: list[str]) -> list[uuid.UUID]:
        """Ids of the organization's skills with these names, creating missing ones."""
        if not names:
            return []
        await self._conn.execute(
            pg_insert(skills)
            .values([{"id": uuid.uuid4(), "org_id": org_id, "name": name} for name in names])
            .on_conflict_do_nothing(index_elements=[skills.c.org_id, skills.c.name])
        )
        rows = (
            await self._conn.execute(
                sa.select(skills.c.id, skills.c.name).where(
                    skills.c.org_id == org_id,
                    sa.func.lower(skills.c.name).in_([name.lower() for name in names]),
                )
            )
        ).all()
        by_name = {str(row.name).lower(): row.id for row in rows}
        return [by_name[name.lower()] for name in names if name.lower() in by_name]

    async def add_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        """Give the person these skills at the default proficiency; held skills stay as they are."""
        if not skill_ids:
            return
        await self._conn.execute(
            pg_insert(employee_skills)
            .values(
                [
                    {
                        "employee_id": employee_id,
                        "skill_id": skill_id,
                        "proficiency": DEFAULT_PROFICIENCY,
                    }
                    for skill_id in skill_ids
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[employee_skills.c.employee_id, employee_skills.c.skill_id]
            )
        )

    async def set_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        """Replace the person's skills; skills they keep keep their proficiency."""
        stale = sa.delete(employee_skills).where(employee_skills.c.employee_id == employee_id)
        if skill_ids:
            stale = stale.where(employee_skills.c.skill_id.not_in(skill_ids))
        await self._conn.execute(stale)
        await self.add_employee_skills(employee_id, skill_ids)

    async def _project_skill_rows(self, project_id: uuid.UUID) -> list[SkillRow]:
        """Return the project's own skill list, in the order it was built."""
        rows = (
            await self._conn.execute(
                sa.select(project_skills.c.skill_id, skills.c.name, project_skills.c.required)
                .select_from(project_skills.join(skills, skills.c.id == project_skills.c.skill_id))
                .where(project_skills.c.project_id == project_id)
                .order_by(project_skills.c.created_at, skills.c.name, project_skills.c.skill_id)
            )
        ).all()
        return [SkillRow(row.skill_id, str(row.name), bool(row.required)) for row in rows]

    async def _wbs_skill_rows(self, project_id: uuid.UUID) -> list[SkillRow]:
        """Skills the work packages require, as the starting list (all required)."""
        rows = (
            await self._conn.execute(
                sa.select(task_skill_requirements.c.skill_id, skills.c.name)
                .select_from(
                    task_skill_requirements.join(
                        wbs_nodes, wbs_nodes.c.id == task_skill_requirements.c.wbs_node_id
                    ).join(skills, skills.c.id == task_skill_requirements.c.skill_id)
                )
                .where(wbs_nodes.c.project_id == project_id)
                .group_by(task_skill_requirements.c.skill_id, skills.c.name)
                .order_by(skills.c.name)
            )
        ).all()
        return [SkillRow(row.skill_id, str(row.name), True) for row in rows]

    async def skill_rows(self, project_id: uuid.UUID) -> list[SkillRow]:
        """The project's skill list; until it is edited, the skills its WBS requires."""
        return await self._project_skill_rows(project_id) or await self._wbs_skill_rows(project_id)

    async def materialize_skills(self, project_id: uuid.UUID) -> None:
        """Before the first edit, store the list derived from the WBS so edits apply to it."""
        if await self._project_skill_rows(project_id):
            return
        derived = await self._wbs_skill_rows(project_id)
        if derived:
            await self._conn.execute(
                pg_insert(project_skills)
                .values(
                    [
                        {
                            "project_id": project_id,
                            "skill_id": row.skill_id,
                            "required": row.required,
                        }
                        for row in derived
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=[project_skills.c.project_id, project_skills.c.skill_id]
                )
            )

    async def holders(
        self, project_id: uuid.UUID, skill_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """Team members who have each skill (any proficiency), by name."""
        out: dict[uuid.UUID, list[str]] = defaultdict(list)
        if not skill_ids:
            return out
        rows = (
            await self._conn.execute(
                sa.select(employee_skills.c.skill_id, employees.c.full_name)
                .select_from(
                    employee_skills.join(
                        project_team_members,
                        (project_team_members.c.employee_id == employee_skills.c.employee_id)
                        & (project_team_members.c.project_id == project_id),
                    ).join(employees, employees.c.id == employee_skills.c.employee_id)
                )
                .where(employee_skills.c.skill_id.in_(skill_ids))
                .order_by(project_team_members.c.created_at, employees.c.full_name)
            )
        ).all()
        for row in rows:
            out[row.skill_id].append(str(row.full_name))
        return out

    async def skills(self, project_id: uuid.UUID) -> list[ProjectSkill]:
        """Return the project's skills with the team members who hold each one."""
        rows = await self.skill_rows(project_id)
        who = await self.holders(project_id, [row.skill_id for row in rows])
        return [
            ProjectSkill(
                skill_id=row.skill_id,
                name=row.name,
                required=row.required,
                holders=who.get(row.skill_id, []),
            )
            for row in rows
        ]

    async def org_skill(self, org_id: uuid.UUID, name: str) -> uuid.UUID | None:
        """Return the id of the organization's skill with this name (case-insensitive), or None."""
        stmt = sa.select(skills.c.id).where(
            skills.c.org_id == org_id, sa.func.lower(skills.c.name) == name.lower()
        )
        return (await self._conn.execute(stmt)).scalar()

    async def add_project_skill(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> None:
        """Add a skill to the project's list."""
        await self._conn.execute(
            sa.insert(project_skills).values(
                project_id=project_id, skill_id=skill_id, required=required
            )
        )

    async def set_required(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> bool:
        """Mark a project skill required or nice-to-have; False when it is not on the list."""
        result = await self._conn.execute(
            sa.update(project_skills)
            .where(project_skills.c.project_id == project_id, project_skills.c.skill_id == skill_id)
            .values(required=required)
        )
        return result.rowcount > 0

    async def rename_skill(self, skill_id: uuid.UUID, name: str) -> None:
        """Rename the organization's skill (every project using it sees the new name)."""
        await self._conn.execute(sa.update(skills).where(skills.c.id == skill_id).values(name=name))

    async def replace_skill(
        self, project_id: uuid.UUID, old_id: uuid.UUID, new_id: uuid.UUID, required: bool
    ) -> None:
        """Point the project at another skill; team members with the old one get the new one."""
        await self._conn.execute(
            sa.delete(project_skills).where(
                project_skills.c.project_id == project_id, project_skills.c.skill_id == old_id
            )
        )
        await self.add_project_skill(project_id, new_id, required)
        holders = sa.select(employee_skills.c.employee_id).where(
            employee_skills.c.skill_id == old_id,
            employee_skills.c.employee_id.in_(
                sa.select(project_team_members.c.employee_id).where(
                    project_team_members.c.project_id == project_id
                )
            ),
        )
        employee_ids: list[uuid.UUID] = list((await self._conn.execute(holders)).scalars())
        for employee_id in employee_ids:
            await self.add_employee_skills(employee_id, [new_id])

    async def remove_skill(self, project_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
        """Take a skill off the project's list; False when it was not on it."""
        result = await self._conn.execute(
            sa.delete(project_skills).where(
                project_skills.c.project_id == project_id, project_skills.c.skill_id == skill_id
            )
        )
        return result.rowcount > 0
