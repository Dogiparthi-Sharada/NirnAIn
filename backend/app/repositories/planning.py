"""Repository of a project's constraints, resource pool, capacity and skill coverage.

Purpose: Persist the constraint set and milestones the PM sets on step 5 and read the people, their
spare capacity and the skills the WBS needs, all under the caller's RLS session.
How it fits: Used by app/api/v1/planning.py; dependency links come from the mixed-in
`DependencyQueries` (app/repositories/dependency_links.py); layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.common import ConstraintMode
from app.domain.planning import (
    CapacityEntry,
    ConstraintSet,
    ConstraintSetInput,
    EmployeeSkill,
    Milestone,
    PoolEmployee,
    average_weekly_hours,
)
from app.repositories import access
from app.repositories.access import ProjectRef
from app.repositories.dependency_links import DependencyQueries
from app.repositories.tables import (
    allocations,
    constraint_sets,
    employee_skills,
    employees,
    milestones,
    skills,
    task_skill_requirements,
    wbs_nodes,
)

SkillLevels = dict[uuid.UUID, tuple[str, int]]


def _default_constraints(project: ProjectRef) -> dict[str, Any]:
    """Return what GET shows before the project has a constraint set (the table defaults).

    The budget and dates start from the project's own values.
    """
    return {
        "budget_total_cents": project.budget_cents,
        "contingency_pct": 0.0,
        "labor_cap_cents": None,
        "infra_cap_cents": None,
        "budget_mode": ConstraintMode.HARD,
        "start_date": project.start_date,
        "must_finish_by": project.target_end_date,
        "deadline_mode": ConstraintMode.HARD,
        "working_days_per_week": 5,
        "capacity_mode": ConstraintMode.SOFT,
        "skill_mode": ConstraintMode.HARD,
        "specialist_answer": None,
        "deadline_answer": None,
        "specialist_rate_confirmed": False,
        "contractor_rate_cents": None,
        "allow_contractors": True,
    }


class PlanningRepository(DependencyQueries):
    """Planning resources. Visibility and write permissions come from RLS."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        """Return the project when the caller can see it, else None."""
        return await access.visible_project(self._conn, project_id)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        """Tell whether the caller may change the project."""
        return await access.can_write_project(self._conn, project_id)

    # ------------------------------------------------------------------ constraints
    async def constraints(self, project: ProjectRef) -> ConstraintSet:
        """Return the project's constraint set and milestones (defaults when none is saved yet)."""
        row = (
            await self._conn.execute(
                sa.select(
                    constraint_sets.c.budget_total_cents,
                    constraint_sets.c.contingency_pct,
                    constraint_sets.c.labor_cap_cents,
                    constraint_sets.c.infra_cap_cents,
                    constraint_sets.c.budget_mode,
                    constraint_sets.c.start_date,
                    constraint_sets.c.must_finish_by,
                    constraint_sets.c.deadline_mode,
                    constraint_sets.c.working_days_per_week,
                    constraint_sets.c.capacity_mode,
                    constraint_sets.c.skill_mode,
                    constraint_sets.c.specialist_answer,
                    constraint_sets.c.deadline_answer,
                    constraint_sets.c.specialist_rate_confirmed,
                    constraint_sets.c.contractor_rate_cents,
                    constraint_sets.c.allow_contractors,
                ).where(constraint_sets.c.project_id == project.id)
            )
        ).first()
        values = dict(row._mapping) if row else _default_constraints(project)
        rows = (
            await self._conn.execute(
                sa.select(
                    milestones.c.id,
                    milestones.c.name,
                    milestones.c.due_date,
                    milestones.c.wbs_node_id,
                    milestones.c.mode,
                )
                .where(milestones.c.project_id == project.id)
                .order_by(milestones.c.due_date, milestones.c.created_at, milestones.c.id)
            )
        ).all()
        return ConstraintSet.model_validate(
            {
                **values,
                "milestones": [
                    Milestone.model_validate(dict(milestone_row._mapping)) for milestone_row in rows
                ],
            }
        )

    async def node_ids(self, project_id: uuid.UUID, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """The subset of `ids` that are WBS nodes of the project."""
        if not ids:
            return set()
        nodes = wbs_nodes
        stmt = sa.select(nodes.c.id).where(nodes.c.project_id == project_id, nodes.c.id.in_(ids))
        return set((await self._conn.execute(stmt)).scalars())

    async def save_constraints(self, project_id: uuid.UUID, body: ConstraintSetInput) -> None:
        """Upsert the constraint set and replace the milestones with the body's list.

        Milestones with a known id are updated, the others inserted; any not in the body are
        deleted.
        """
        values = body.values()
        values["contingency_pct"] = Decimal(str(round(values["contingency_pct"], 2)))
        await self._conn.execute(
            pg_insert(constraint_sets)
            .values(project_id=project_id, **values)
            .on_conflict_do_update(index_elements=[constraint_sets.c.project_id], set_=values)
        )

        existing: set[uuid.UUID] = set(
            (
                await self._conn.execute(
                    sa.select(milestones.c.id).where(milestones.c.project_id == project_id)
                )
            ).scalars()
        )
        keep = {milestone.id for milestone in body.milestones if milestone.id in existing}
        await self._conn.execute(
            sa.delete(milestones).where(
                milestones.c.project_id == project_id, milestones.c.id.not_in(keep)
            )
            if keep
            else sa.delete(milestones).where(milestones.c.project_id == project_id)
        )
        for milestone in body.milestones:
            fields = {
                "name": milestone.name,
                "due_date": milestone.due_date,
                "wbs_node_id": milestone.wbs_node_id,
                "mode": milestone.mode,
            }
            if milestone.id in keep:
                await self._conn.execute(
                    sa.update(milestones)
                    .where(milestones.c.id == milestone.id, milestones.c.project_id == project_id)
                    .values(fields)
                )
            else:
                await self._conn.execute(
                    sa.insert(milestones).values(
                        id=milestone.id or uuid.uuid4(), project_id=project_id, **fields
                    )
                )

    # ------------------------------------------------------------------ resources
    def _pool_select(self, org_id: uuid.UUID) -> sa.Select[Any]:
        """Return the select of the organization's active employees, by name."""
        staff = employees
        return (
            sa.select(
                staff.c.id.label("employee_id"),
                staff.c.full_name,
                staff.c.role_title,
                staff.c.weekly_capacity_hours,
                staff.c.hourly_rate_cents,
                staff.c.is_contractor,
            )
            .where(staff.c.org_id == org_id, staff.c.active.is_(True))
            .order_by(staff.c.full_name, staff.c.id)
        )

    async def _skills_of(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[EmployeeSkill]]:
        """Return each employee's skills with proficiency, by skill name."""
        out: dict[uuid.UUID, list[EmployeeSkill]] = defaultdict(list)
        if ids:
            held, skill_table = employee_skills, skills
            for row in (
                await self._conn.execute(
                    sa.select(
                        held.c.employee_id, held.c.skill_id, skill_table.c.name, held.c.proficiency
                    )
                    .select_from(held.join(skill_table, skill_table.c.id == held.c.skill_id))
                    .where(held.c.employee_id.in_(ids))
                    .order_by(held.c.employee_id, skill_table.c.name)
                )
            ).all():
                out[row.employee_id].append(
                    EmployeeSkill(skill_id=row.skill_id, name=row.name, proficiency=row.proficiency)
                )
        return out

    async def employees(self, org_id: uuid.UUID) -> list[PoolEmployee]:
        """List the organization's active employees with their skills."""
        rows = (await self._conn.execute(self._pool_select(org_id))).all()
        skills_by_employee = await self._skills_of([row.employee_id for row in rows])
        return [
            PoolEmployee.model_validate(
                {**row._mapping, "skills": skills_by_employee.get(row.employee_id, [])}
            )
            for row in rows
        ]

    async def capacity(self, project: ProjectRef, weeks: Sequence[date]) -> list[CapacityEntry]:
        """Active org employees with their average weekly hours on other projects in `weeks`."""
        staff, bookings = employees, allocations
        # Hours booked on other projects in the window, summed per person; the outer join
        # below keeps people with no bookings (0 hours).
        other = (
            sa.select(bookings.c.employee_id, sa.func.sum(bookings.c.hours).label("total"))
            .where(
                bookings.c.project_id != project.id,
                bookings.c.week_start >= min(weeks),
                bookings.c.week_start <= max(weeks),
                bookings.c.employee_id.in_(
                    sa.select(staff.c.id).where(staff.c.org_id == project.org_id)
                ),
            )
            .group_by(bookings.c.employee_id)
            .subquery()
        )
        stmt = (
            self._pool_select(project.org_id)
            .add_columns(sa.func.coalesce(other.c.total, 0).label("total"))
            .outerjoin(other, other.c.employee_id == staff.c.id)
        )
        rows = (await self._conn.execute(stmt)).all()
        skills_by_employee = await self._skills_of([row.employee_id for row in rows])
        return [
            CapacityEntry.model_validate(
                {
                    **{
                        column: value for column, value in row._mapping.items() if column != "total"
                    },
                    "allocated_hours": average_weekly_hours(float(row.total), len(weeks)),
                    "skills": skills_by_employee.get(row.employee_id, []),
                }
            )
            for row in rows
        ]

    async def required_skills(self, project_id: uuid.UUID) -> SkillLevels:
        """Return each skill the WBS needs with the highest minimum proficiency asked for it."""
        requirements, skill_table, nodes = task_skill_requirements, skills, wbs_nodes
        rows = (
            await self._conn.execute(
                sa.select(
                    requirements.c.skill_id,
                    skill_table.c.name,
                    sa.func.max(requirements.c.min_proficiency).label("level"),
                )
                .select_from(
                    requirements.join(nodes, nodes.c.id == requirements.c.wbs_node_id).join(
                        skill_table, skill_table.c.id == requirements.c.skill_id
                    )
                )
                .where(nodes.c.project_id == project_id)
                .group_by(requirements.c.skill_id, skill_table.c.name)
            )
        ).all()
        return {row.skill_id: (str(row.name), int(row.level)) for row in rows}

    async def held_skills(self, org_id: uuid.UUID) -> SkillLevels:
        """Return each skill active employees hold with the best proficiency anyone has."""
        held, staff, skill_table = employee_skills, employees, skills
        rows = (
            await self._conn.execute(
                sa.select(
                    held.c.skill_id,
                    skill_table.c.name,
                    sa.func.max(held.c.proficiency).label("level"),
                )
                .select_from(
                    held.join(staff, staff.c.id == held.c.employee_id).join(
                        skill_table, skill_table.c.id == held.c.skill_id
                    )
                )
                .where(staff.c.org_id == org_id, staff.c.active.is_(True))
                .group_by(held.c.skill_id, skill_table.c.name)
            )
        ).all()
        return {row.skill_id: (str(row.name), int(row.level)) for row in rows}
