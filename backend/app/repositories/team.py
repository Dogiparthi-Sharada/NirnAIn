"""The team on a project (`project_team_members`) and the skills it needs (`project_skills`)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.planning import average_weekly_hours
from app.domain.team import ProjectSkill, TeamMember
from app.repositories import access, plan_lock
from app.repositories.access import ProjectRef
from app.repositories.tables import (
    allocations,
    constraint_sets,
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
    skill_id: uuid.UUID
    name: str
    required: bool


class TeamRepository:
    """Team and skills persistence. Visibility and write permissions come from RLS."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        return await access.visible_project(self._conn, project_id)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return await access.can_write_project(self._conn, project_id)

    async def applied_version(self, project_id: uuid.UUID) -> int | None:
        return await plan_lock.applied_version(self._conn, project_id)

    async def window(self, project: ProjectRef) -> tuple[date | None, date | None]:
        """Planning window: the constraint dates, falling back to the project's dates."""
        cs = constraint_sets
        row = (
            await self._conn.execute(
                sa.select(cs.c.start_date, cs.c.must_finish_by).where(cs.c.project_id == project.id)
            )
        ).first()
        start = row.start_date if row else None
        end = row.must_finish_by if row else None
        return start or project.start_date, end or project.target_end_date

    # ------------------------------------------------------------------ team
    async def members(self, project: ProjectRef, weeks: Sequence[date]) -> list[TeamMember]:
        """The team, with average weekly hours already booked on other projects in `weeks`."""
        t, e, al = project_team_members, employees, allocations
        other = (
            sa.select(al.c.employee_id, sa.func.sum(al.c.hours).label("total"))
            .where(
                al.c.project_id != project.id,
                al.c.week_start >= min(weeks),
                al.c.week_start <= max(weeks),
                al.c.employee_id.in_(
                    sa.select(t.c.employee_id).where(t.c.project_id == project.id)
                ),
            )
            .group_by(al.c.employee_id)
            .subquery()
        )
        rows = (
            await self._conn.execute(
                sa.select(
                    t.c.employee_id,
                    e.c.full_name,
                    sa.func.coalesce(t.c.role_title, e.c.role_title).label("role_title"),
                    t.c.hours_per_week,
                    sa.func.coalesce(other.c.total, 0).label("total"),
                )
                .select_from(
                    t.join(e, e.c.id == t.c.employee_id).outerjoin(
                        other, other.c.employee_id == t.c.employee_id
                    )
                )
                .where(t.c.project_id == project.id)
                .order_by(t.c.created_at, e.c.full_name, t.c.employee_id)
            )
        ).all()
        names = await self._skill_names([r.employee_id for r in rows])
        return [
            TeamMember(
                employee_id=r.employee_id,
                full_name=r.full_name,
                role_title=r.role_title,
                hours_per_week=float(r.hours_per_week),
                booked_hours=average_weekly_hours(float(r.total), len(weeks)),
                skills=names.get(r.employee_id, []),
            )
            for r in rows
        ]

    async def _skill_names(self, employee_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        out: dict[uuid.UUID, list[str]] = defaultdict(list)
        if employee_ids:
            es, s = employee_skills, skills
            for row in (
                await self._conn.execute(
                    sa.select(es.c.employee_id, s.c.name)
                    .select_from(es.join(s, s.c.id == es.c.skill_id))
                    .where(es.c.employee_id.in_(employee_ids))
                    .order_by(es.c.employee_id, s.c.name)
                )
            ).all():
                out[row.employee_id].append(str(row.name))
        return out

    async def member_names(self, project_id: uuid.UUID) -> dict[uuid.UUID, str]:
        t, e = project_team_members, employees
        rows = (
            await self._conn.execute(
                sa.select(t.c.employee_id, e.c.full_name)
                .select_from(t.join(e, e.c.id == t.c.employee_id))
                .where(t.c.project_id == project_id)
            )
        ).all()
        return {r.employee_id: str(r.full_name) for r in rows}

    async def find_employee(self, org_id: uuid.UUID, full_name: str) -> uuid.UUID | None:
        """An active employee of the organization with this name (case-insensitive)."""
        e = employees
        stmt = (
            sa.select(e.c.id)
            .where(
                e.c.org_id == org_id,
                e.c.active.is_(True),
                sa.func.lower(e.c.full_name) == full_name.lower(),
            )
            .order_by(e.c.full_name, e.c.id)
            .limit(1)
        )
        return (await self._conn.execute(stmt)).scalar()

    async def create_employee(
        self, org_id: uuid.UUID, full_name: str, role_title: str, hours_per_week: int
    ) -> uuid.UUID:
        employee_id = uuid.uuid4()
        await self._conn.execute(
            sa.insert(employees).values(
                id=employee_id,
                org_id=org_id,
                full_name=full_name,
                role_title=role_title,
                weekly_capacity_hours=hours_per_week,
            )
        )
        return employee_id

    async def rename_employee(self, employee_id: uuid.UUID, full_name: str) -> None:
        e = employees
        await self._conn.execute(
            sa.update(e).where(e.c.id == employee_id).values(full_name=full_name)
        )

    async def add_member(
        self, project_id: uuid.UUID, employee_id: uuid.UUID, role_title: str, hours_per_week: int
    ) -> None:
        await self._conn.execute(
            sa.insert(project_team_members).values(
                project_id=project_id,
                employee_id=employee_id,
                role_title=role_title,
                hours_per_week=hours_per_week,
            )
        )

    async def update_member(
        self, project_id: uuid.UUID, employee_id: uuid.UUID, changes: dict[str, Any]
    ) -> bool:
        t = project_team_members
        result = await self._conn.execute(
            sa.update(t)
            .where(t.c.project_id == project_id, t.c.employee_id == employee_id)
            .values(**changes)
        )
        return result.rowcount > 0

    async def remove_member(self, project_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        t = project_team_members
        result = await self._conn.execute(
            sa.delete(t).where(t.c.project_id == project_id, t.c.employee_id == employee_id)
        )
        return result.rowcount > 0

    async def ensure_skills(self, org_id: uuid.UUID, names: list[str]) -> list[uuid.UUID]:
        """Ids of the organization's skills with these names, creating missing ones."""
        if not names:
            return []
        s = skills
        await self._conn.execute(
            pg_insert(s)
            .values([{"id": uuid.uuid4(), "org_id": org_id, "name": n} for n in names])
            .on_conflict_do_nothing(index_elements=[s.c.org_id, s.c.name])
        )
        rows = (
            await self._conn.execute(
                sa.select(s.c.id, s.c.name).where(
                    s.c.org_id == org_id, sa.func.lower(s.c.name).in_([n.lower() for n in names])
                )
            )
        ).all()
        by_name = {str(r.name).lower(): r.id for r in rows}
        return [by_name[n.lower()] for n in names if n.lower() in by_name]

    async def add_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        if not skill_ids:
            return
        es = employee_skills
        await self._conn.execute(
            pg_insert(es)
            .values(
                [
                    {"employee_id": employee_id, "skill_id": s, "proficiency": DEFAULT_PROFICIENCY}
                    for s in skill_ids
                ]
            )
            .on_conflict_do_nothing(index_elements=[es.c.employee_id, es.c.skill_id])
        )

    async def set_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        """Replace the person's skills; skills they keep keep their proficiency."""
        es = employee_skills
        stale = sa.delete(es).where(es.c.employee_id == employee_id)
        if skill_ids:
            stale = stale.where(es.c.skill_id.not_in(skill_ids))
        await self._conn.execute(stale)
        await self.add_employee_skills(employee_id, skill_ids)

    # ------------------------------------------------------------------ skills
    async def _project_skill_rows(self, project_id: uuid.UUID) -> list[SkillRow]:
        ps, s = project_skills, skills
        rows = (
            await self._conn.execute(
                sa.select(ps.c.skill_id, s.c.name, ps.c.required)
                .select_from(ps.join(s, s.c.id == ps.c.skill_id))
                .where(ps.c.project_id == project_id)
                .order_by(ps.c.created_at, s.c.name, ps.c.skill_id)
            )
        ).all()
        return [SkillRow(r.skill_id, str(r.name), bool(r.required)) for r in rows]

    async def _wbs_skill_rows(self, project_id: uuid.UUID) -> list[SkillRow]:
        """Skills the work packages require, as the starting list (all required)."""
        t, s, n = task_skill_requirements, skills, wbs_nodes
        rows = (
            await self._conn.execute(
                sa.select(t.c.skill_id, s.c.name)
                .select_from(t.join(n, n.c.id == t.c.wbs_node_id).join(s, s.c.id == t.c.skill_id))
                .where(n.c.project_id == project_id)
                .group_by(t.c.skill_id, s.c.name)
                .order_by(s.c.name)
            )
        ).all()
        return [SkillRow(r.skill_id, str(r.name), True) for r in rows]

    async def skill_rows(self, project_id: uuid.UUID) -> list[SkillRow]:
        """The project's skill list; until it is edited, the skills its WBS requires."""
        return await self._project_skill_rows(project_id) or await self._wbs_skill_rows(project_id)

    async def materialize_skills(self, project_id: uuid.UUID) -> None:
        """Before the first edit, store the list derived from the WBS so edits apply to it."""
        if await self._project_skill_rows(project_id):
            return
        derived = await self._wbs_skill_rows(project_id)
        if derived:
            ps = project_skills
            await self._conn.execute(
                pg_insert(ps)
                .values(
                    [
                        {"project_id": project_id, "skill_id": r.skill_id, "required": r.required}
                        for r in derived
                    ]
                )
                .on_conflict_do_nothing(index_elements=[ps.c.project_id, ps.c.skill_id])
            )

    async def holders(
        self, project_id: uuid.UUID, skill_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """Team members who have each skill (any proficiency), by name."""
        out: dict[uuid.UUID, list[str]] = defaultdict(list)
        if not skill_ids:
            return out
        es, t, e = employee_skills, project_team_members, employees
        rows = (
            await self._conn.execute(
                sa.select(es.c.skill_id, e.c.full_name)
                .select_from(
                    es.join(
                        t, (t.c.employee_id == es.c.employee_id) & (t.c.project_id == project_id)
                    ).join(e, e.c.id == es.c.employee_id)
                )
                .where(es.c.skill_id.in_(skill_ids))
                .order_by(t.c.created_at, e.c.full_name)
            )
        ).all()
        for r in rows:
            out[r.skill_id].append(str(r.full_name))
        return out

    async def skills(self, project_id: uuid.UUID) -> list[ProjectSkill]:
        rows = await self.skill_rows(project_id)
        who = await self.holders(project_id, [r.skill_id for r in rows])
        return [
            ProjectSkill(
                skill_id=r.skill_id,
                name=r.name,
                required=r.required,
                holders=who.get(r.skill_id, []),
            )
            for r in rows
        ]

    async def org_skill(self, org_id: uuid.UUID, name: str) -> uuid.UUID | None:
        s = skills
        stmt = sa.select(s.c.id).where(
            s.c.org_id == org_id, sa.func.lower(s.c.name) == name.lower()
        )
        return (await self._conn.execute(stmt)).scalar()

    async def add_project_skill(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> None:
        await self._conn.execute(
            sa.insert(project_skills).values(
                project_id=project_id, skill_id=skill_id, required=required
            )
        )

    async def set_required(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> bool:
        ps = project_skills
        result = await self._conn.execute(
            sa.update(ps)
            .where(ps.c.project_id == project_id, ps.c.skill_id == skill_id)
            .values(required=required)
        )
        return result.rowcount > 0

    async def rename_skill(self, skill_id: uuid.UUID, name: str) -> None:
        await self._conn.execute(sa.update(skills).where(skills.c.id == skill_id).values(name=name))

    async def replace_skill(
        self, project_id: uuid.UUID, old_id: uuid.UUID, new_id: uuid.UUID, required: bool
    ) -> None:
        """Point the project at another skill; team members with the old one get the new one."""
        ps, es, t = project_skills, employee_skills, project_team_members
        await self._conn.execute(
            sa.delete(ps).where(ps.c.project_id == project_id, ps.c.skill_id == old_id)
        )
        await self.add_project_skill(project_id, new_id, required)
        holders = sa.select(es.c.employee_id).where(
            es.c.skill_id == old_id,
            es.c.employee_id.in_(sa.select(t.c.employee_id).where(t.c.project_id == project_id)),
        )
        employee_ids: list[uuid.UUID] = list((await self._conn.execute(holders)).scalars())
        for employee_id in employee_ids:
            await self.add_employee_skills(employee_id, [new_id])

    async def remove_skill(self, project_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
        ps = project_skills
        result = await self._conn.execute(
            sa.delete(ps).where(ps.c.project_id == project_id, ps.c.skill_id == skill_id)
        )
        return result.rowcount > 0
