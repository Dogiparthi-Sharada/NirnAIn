"""Repository of the team on a project (`project_team_members`) and its people's records.

Purpose: Put people on and off a project, keep their hours, role, worker type and rate, and show how
much of their time other projects already booked (caller's RLS session).
How it fits: Used by app/api/v1/team.py and the step-7 change repositories; skill reads and writes
come from the mixed-in `TeamSkillQueries` (app/repositories/team_skills.py); layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.planning import average_weekly_hours
from app.domain.team import TeamMember
from app.repositories import access
from app.repositories.access import ProjectRef
from app.repositories.tables import (
    allocations,
    constraint_sets,
    employee_skills,
    employees,
    project_team_members,
    skills,
    wbs_nodes,
)
from app.repositories.team_skills import TeamSkillQueries


class TeamRepository(TeamSkillQueries):
    """Team and skills persistence. Visibility and write permissions come from RLS."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        """Return the project when the caller can see it, else None."""
        return await access.visible_project(self._conn, project_id)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        """Tell whether the caller may change the project."""
        return await access.can_write_project(self._conn, project_id)

    async def window(self, project: ProjectRef) -> tuple[date | None, date | None]:
        """Planning window: the constraint dates, falling back to the project's dates."""
        row = (
            await self._conn.execute(
                sa.select(constraint_sets.c.start_date, constraint_sets.c.must_finish_by).where(
                    constraint_sets.c.project_id == project.id
                )
            )
        ).first()
        start = row.start_date if row else None
        end = row.must_finish_by if row else None
        return start or project.start_date, end or project.target_end_date

    # ------------------------------------------------------------------ team
    async def members(self, project: ProjectRef, weeks: Sequence[date]) -> list[TeamMember]:
        """The team, with average weekly hours already booked on other projects in `weeks`."""
        member, employee, booking = project_team_members, employees, allocations
        # Hours booked on other projects in the window, summed per person; the outer join
        # below keeps people with no bookings (0 hours).
        other = (
            sa.select(booking.c.employee_id, sa.func.sum(booking.c.hours).label("total"))
            .where(
                booking.c.project_id != project.id,
                booking.c.week_start >= min(weeks),
                booking.c.week_start <= max(weeks),
                booking.c.employee_id.in_(
                    sa.select(member.c.employee_id).where(member.c.project_id == project.id)
                ),
            )
            .group_by(booking.c.employee_id)
            .subquery()
        )
        rows = (
            await self._conn.execute(
                sa.select(
                    member.c.employee_id,
                    employee.c.full_name,
                    sa.func.coalesce(member.c.role_title, employee.c.role_title).label(
                        "role_title"
                    ),
                    member.c.hours_per_week,
                    employee.c.is_contractor,
                    employee.c.hourly_rate_cents,
                    sa.func.coalesce(other.c.total, 0).label("total"),
                )
                .select_from(
                    member.join(employee, employee.c.id == member.c.employee_id).outerjoin(
                        other, other.c.employee_id == member.c.employee_id
                    )
                )
                .where(member.c.project_id == project.id)
                .order_by(member.c.created_at, employee.c.full_name, member.c.employee_id)
            )
        ).all()
        names = await self._skill_names([row.employee_id for row in rows])
        return [
            TeamMember(
                employee_id=row.employee_id,
                full_name=row.full_name,
                role_title=row.role_title,
                hours_per_week=float(row.hours_per_week),
                worker_type="contractor" if row.is_contractor else "employee",
                hourly_rate_cents=row.hourly_rate_cents,
                booked_hours=average_weekly_hours(float(row.total), len(weeks)),
                skills=names.get(row.employee_id, []),
            )
            for row in rows
        ]

    async def _skill_names(self, employee_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        """Return each employee's skill names, alphabetically."""
        out: dict[uuid.UUID, list[str]] = defaultdict(list)
        if employee_ids:
            held, skill_table = employee_skills, skills
            for row in (
                await self._conn.execute(
                    sa.select(held.c.employee_id, skill_table.c.name)
                    .select_from(held.join(skill_table, skill_table.c.id == held.c.skill_id))
                    .where(held.c.employee_id.in_(employee_ids))
                    .order_by(held.c.employee_id, skill_table.c.name)
                )
            ).all():
                out[row.employee_id].append(str(row.name))
        return out

    async def member_names(self, project_id: uuid.UUID) -> dict[uuid.UUID, str]:
        """Return the full name of everyone on the project's team, by employee id."""
        members, staff = project_team_members, employees
        rows = (
            await self._conn.execute(
                sa.select(members.c.employee_id, staff.c.full_name)
                .select_from(members.join(staff, staff.c.id == members.c.employee_id))
                .where(members.c.project_id == project_id)
            )
        ).all()
        return {row.employee_id: str(row.full_name) for row in rows}

    async def find_employee(self, org_id: uuid.UUID, full_name: str) -> uuid.UUID | None:
        """An active employee of the organization with this name (case-insensitive)."""
        stmt = (
            sa.select(employees.c.id)
            .where(
                employees.c.org_id == org_id,
                employees.c.active.is_(True),
                sa.func.lower(employees.c.full_name) == full_name.lower(),
            )
            .order_by(employees.c.full_name, employees.c.id)
            .limit(1)
        )
        return (await self._conn.execute(stmt)).scalar()

    async def create_employee(
        self,
        org_id: uuid.UUID,
        full_name: str,
        role_title: str,
        hours_per_week: int,
        *,
        active: bool = True,
        is_contractor: bool = False,
        hourly_rate_cents: int | None = None,
    ) -> uuid.UUID:
        """A new organization employee. Step 7 creates newcomers inactive (a candidate the
        re-plan can staff) and activates them when the change is approved."""
        employee_id = uuid.uuid4()
        await self._conn.execute(
            sa.insert(employees).values(
                id=employee_id,
                org_id=org_id,
                full_name=full_name,
                role_title=role_title,
                weekly_capacity_hours=hours_per_week,
                active=active,
                is_contractor=is_contractor,
                hourly_rate_cents=hourly_rate_cents,
            )
        )
        return employee_id

    async def employee_rate(self, employee_id: uuid.UUID) -> int | None:
        """The hourly rate on the organization's record (None when there is none)."""
        stmt = sa.select(employees.c.hourly_rate_cents).where(employees.c.id == employee_id)
        rate = (await self._conn.execute(stmt)).scalar()
        return None if rate is None else int(rate)

    async def set_worker_type(self, employee_id: uuid.UUID, is_contractor: bool) -> None:
        """Mark the organization's record as a contractor or an employee."""
        await self._conn.execute(
            sa.update(employees)
            .where(employees.c.id == employee_id)
            .values(is_contractor=is_contractor)
        )

    async def confirmed_contractor_rate(self, project_id: uuid.UUID) -> int | None:
        """The project's contractor rate once the PM confirmed it (None otherwise)."""
        sets = constraint_sets
        row = (
            await self._conn.execute(
                sa.select(sets.c.contractor_rate_cents, sets.c.specialist_rate_confirmed).where(
                    sets.c.project_id == project_id
                )
            )
        ).first()
        if row is None or not row.specialist_rate_confirmed:
            return None
        return None if row.contractor_rate_cents is None else int(row.contractor_rate_cents)

    async def set_hourly_rate(self, employee_id: uuid.UUID, hourly_rate_cents: int | None) -> None:
        """The organization employee's rate (None: planned at the optimizer's default)."""
        await self._conn.execute(
            sa.update(employees)
            .where(employees.c.id == employee_id)
            .values(hourly_rate_cents=hourly_rate_cents)
        )

    async def rename_employee(self, employee_id: uuid.UUID, full_name: str) -> None:
        """Change the name on the organization's employee record."""
        await self._conn.execute(
            sa.update(employees).where(employees.c.id == employee_id).values(full_name=full_name)
        )

    async def add_member(
        self, project_id: uuid.UUID, employee_id: uuid.UUID, role_title: str, hours_per_week: int
    ) -> None:
        """Put an employee on the project's team with a role and weekly hours."""
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
        """Change a team member's project role or hours; False when they are not on the team."""
        result = await self._conn.execute(
            sa.update(project_team_members)
            .where(
                project_team_members.c.project_id == project_id,
                project_team_members.c.employee_id == employee_id,
            )
            .values(**changes)
        )
        return result.rowcount > 0

    async def remove_member(self, project_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        """Take a person off the team; False when they weren't on it.

        Business rule (RES-017): the project's work packages they own lose their owner, so
        step 4 doesn't name someone who is off the project (step 7 departures do the same).
        """
        member = project_team_members
        result = await self._conn.execute(
            sa.delete(member).where(
                member.c.project_id == project_id, member.c.employee_id == employee_id
            )
        )
        if result.rowcount == 0:
            return False
        await self._conn.execute(
            sa.update(wbs_nodes)
            .where(
                wbs_nodes.c.project_id == project_id,
                wbs_nodes.c.owner_employee_id == employee_id,
            )
            .values(owner_employee_id=None)
        )
        return True
