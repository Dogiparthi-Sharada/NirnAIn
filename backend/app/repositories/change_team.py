"""Step 7 team writes: people joining or leaving the project through an approved change.

What: reserves an organization employee for each newcomer when the change is recorded,
releases the reservations nobody uses, and on approval writes who joins (team row, skills,
rate) and who leaves (team row, work package ownership), plus the change's new requirements.
Why: after Plan v1 the team is locked (409 on the Team screen); mid-project hires, specialists,
contractors and departures go through step 7 instead, and only approval changes the inputs.
How it fits: `ChangeRepository` (app/repositories/changes.py) extends this class; everything
runs on the caller's RLS session and reuses the Team screen's helpers (`TeamRepository`).
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.changes.team import is_addition
from app.domain.changes import AddedMember, DetectedChange, PlanOverrides
from app.domain.common import ScopeCategory
from app.repositories.access import ProjectRef
from app.repositories.change_plans import ChangePlanReads
from app.repositories.intake import IntakeRepository
from app.repositories.tables import (
    allocations,
    employees,
    project_team_members,
    wbs_nodes,
)
from app.repositories.team import TeamRepository


class ChangeTeamWrites(ChangePlanReads):
    """Who joins and who leaves, written under the caller's RLS session."""

    async def write_team(self, project: ProjectRef, overrides: PlanOverrides) -> None:
        """The approved change's departures and newcomers (after `materialize_team`)."""
        await self._remove_members(project, overrides.remove_members or [])
        for member in overrides.add_members or []:
            await self._add_member(project, member)

    async def reserve_members(
        self, project: ProjectRef, found: list[DetectedChange]
    ) -> list[DetectedChange]:
        """Give every newcomer an organization employee before the re-plan, so its plan
        can name them (planned assignments reference employees).

        Someone the organization already has (active, same name, case-insensitive) is
        reused. Anyone else becomes an inactive candidate: outside the organization's pool
        and every team until the change is approved (`_add_member` activates them), and
        deleted again when they don't join (`release_members`).
        """
        team = TeamRepository(self._connection)
        reserved: list[DetectedChange] = []
        for change in found:
            member = change.member
            if not is_addition(change) or member is None or change.target is not None:
                reserved.append(change)
                continue
            employee_id = await team.find_employee(project.org_id, member.full_name)
            if employee_id is None:
                employee_id = await team.create_employee(
                    project.org_id,
                    member.full_name,
                    member.role_title,
                    member.hours_per_week,
                    active=False,
                    is_contractor=member.is_contractor,
                    hourly_rate_cents=member.hourly_rate_cents,
                )
            reserved.append(change.model_copy(update={"target": employee_id}))
        return reserved

    async def release_members(self, project: ProjectRef, employee_ids: list[uuid.UUID]) -> None:
        """Delete the candidates reserved for newcomers who don't join (rejected or declined).

        Only inactive records that are on no team and booked nowhere are deleted, so a
        reused employee is never touched; the unapplied proposal's assignments go with them.
        """
        if not employee_ids:
            return
        await self._connection.execute(
            sa.delete(employees).where(
                employees.c.id.in_(employee_ids),
                employees.c.org_id == project.org_id,
                employees.c.active.is_(False),
                ~sa.exists().where(project_team_members.c.employee_id == employees.c.id),
                ~sa.exists().where(allocations.c.employee_id == employees.c.id),
            )
        )

    async def add_requirements(self, project_id: uuid.UUID, texts: list[str]) -> None:
        """New requirements from the approved change become accepted scope items ("Yours")."""
        intake = IntakeRepository(self._connection)
        for requirement in texts:
            await intake.create_item(project_id, ScopeCategory.REQUIREMENT, requirement)

    async def materialize_team(self, project: ProjectRef) -> None:
        """A project still planned with the organization's pool (no team yet) gets that pool
        as its team before people join or leave: otherwise adding one person would leave
        only them in every later plan."""
        member = project_team_members
        has_team = await self._connection.execute(
            sa.select(member.c.employee_id).where(member.c.project_id == project.id).limit(1)
        )
        if has_team.first() is not None:
            return
        pool = sa.select(
            sa.literal(project.id, type_=member.c.project_id.type),
            employees.c.id,
            employees.c.weekly_capacity_hours,
            employees.c.role_title,
        ).where(
            employees.c.org_id == project.org_id,
            employees.c.active.is_(True),
            employees.c.weekly_capacity_hours >= 1,
        )
        await self._connection.execute(
            sa.insert(member).from_select(
                ["project_id", "employee_id", "hours_per_week", "role_title"], pool
            )
        )

    async def _remove_members(self, project: ProjectRef, employee_ids: list[uuid.UUID]) -> None:
        """Take people off the team (they stay in the organization).

        Business rule for the work packages they own: approving applied the plan first, and
        that made whoever the plan gives a package its owner. Packages still owned by someone
        who left (planned for an outside contractor profile, or not planned at all) lose
        their owner rather than name someone who is off the project.
        """
        if not employee_ids:
            return
        await self._connection.execute(
            sa.delete(project_team_members).where(
                project_team_members.c.project_id == project.id,
                project_team_members.c.employee_id.in_(employee_ids),
            )
        )
        await self._connection.execute(
            sa.update(wbs_nodes)
            .where(
                wbs_nodes.c.project_id == project.id,
                wbs_nodes.c.owner_employee_id.in_(employee_ids),
            )
            .values(owner_employee_id=None)
        )

    async def _add_member(self, project: ProjectRef, member: AddedMember) -> None:
        """Put a newcomer on the team: activate their employee (a candidate becomes a real
        one; a reused one stays as is), set the rate if given, and add their skills."""
        activate: dict[str, Any] = {"active": True}
        if member.hourly_rate_cents is not None:
            activate["hourly_rate_cents"] = member.hourly_rate_cents
        await self._connection.execute(
            sa.update(employees)
            .where(employees.c.id == member.employee_id, employees.c.org_id == project.org_id)
            .values(**activate)
        )
        joining = {"hours_per_week": member.hours_per_week, "role_title": member.role_title}
        await self._connection.execute(
            pg_insert(project_team_members)
            .values(project_id=project.id, employee_id=member.employee_id, **joining)
            .on_conflict_do_update(
                index_elements=[
                    project_team_members.c.project_id,
                    project_team_members.c.employee_id,
                ],
                set_=joining,
            )
        )
        team = TeamRepository(self._connection)
        await team.add_employee_skills(
            member.employee_id, await team.ensure_skills(project.org_id, member.skills)
        )
