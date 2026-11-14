"""Read side of step 7 under the caller's RLS session: the plan in force, run results and
the project inputs a change is read against.

`ChangeRepository` (app/repositories/changes.py) extends this class; the API only sees that.
Everything here runs as the calling user, so RLS decides which projects, runs and planned
assignments are visible; nothing in step 7 uses the service connection.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.changes.compare import schedule_dates
from app.changes.context import ChangeContext, NodeRow, PersonRef, PlanRow, RunMeta, TaskRef
from app.domain.common import SolverStatus
from app.repositories import access
from app.repositories.access import ProjectRef
from app.repositories.tables import (
    constraint_sets,
    dependencies,
    employees,
    optimization_runs,
    planned_assignments,
    project_team_members,
    wbs_nodes_with_code,
)
from app.repositories.team import TeamRepository


@dataclass(frozen=True, slots=True)
class InForce:
    """The applied plan a change is measured against."""

    run_id: uuid.UUID
    version: int | None
    expected_cost_cents: int | None
    completion_date: date | None


@dataclass(frozen=True, slots=True)
class RunState:
    """What approval needs to know about a proposed run."""

    id: uuid.UUID
    project_id: uuid.UUID
    status: SolverStatus
    applied: bool
    version: int | None


class ChangePlanReads:
    """Plans, runs and project inputs as the caller sees them."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        """The project if the caller can see it (None otherwise)."""
        return await access.visible_project(self._connection, project_id)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        """Whether the caller may change the project (`private.can_write_project`)."""
        return await access.can_write_project(self._connection, project_id)

    # ------------------------------------------------------------------ plan in force
    async def in_force(self, project_id: uuid.UUID) -> InForce | None:
        """The plan in force: the run applied last."""
        statement = (
            sa.select(
                optimization_runs.c.id,
                optimization_runs.c.version,
                optimization_runs.c.expected_cost_cents,
                optimization_runs.c.completion_date,
            )
            .where(
                optimization_runs.c.project_id == project_id,
                optimization_runs.c.applied_at.is_not(None),
            )
            .order_by(
                optimization_runs.c.applied_at.desc(),
                optimization_runs.c.version.desc().nulls_last(),
            )
            .limit(1)
        )
        row = (await self._connection.execute(statement)).first()
        if row is None:
            return None
        return InForce(row.id, row.version, row.expected_cost_cents, row.completion_date)

    async def run(self, run_id: uuid.UUID) -> RunState | None:
        """A run's status, project and whether it is applied (None when not visible)."""
        statement = sa.select(
            optimization_runs.c.id,
            optimization_runs.c.project_id,
            optimization_runs.c.status,
            optimization_runs.c.applied_at,
            optimization_runs.c.version,
        ).where(optimization_runs.c.id == run_id)
        row = (await self._connection.execute(statement)).first()
        if row is None:
            return None
        return RunState(
            row.id,
            row.project_id,
            SolverStatus(row.status),
            row.applied_at is not None,
            row.version,
        )

    async def run_meta(self, run_id: uuid.UUID) -> RunMeta | None:
        """A run's KPIs, frozen input snapshot and day-level schedule, for the comparison."""
        statement = sa.select(
            optimization_runs.c.id,
            optimization_runs.c.project_id,
            optimization_runs.c.status,
            optimization_runs.c.version,
            optimization_runs.c.expected_cost_cents,
            optimization_runs.c.completion_date,
            optimization_runs.c.input_snapshot,
            optimization_runs.c.result["schedule"].label("schedule"),
        ).where(optimization_runs.c.id == run_id)
        row = (await self._connection.execute(statement)).first()
        if row is None:
            return None
        return RunMeta(
            row.id,
            row.project_id,
            SolverStatus(row.status),
            row.version,
            row.expected_cost_cents,
            row.completion_date,
            dict(row.input_snapshot or {}),
            schedule_dates(row.schedule),
        )

    async def plan_rows(self, run_id: uuid.UUID) -> list[PlanRow]:
        """The run's planned assignments (week indexes from its planning start)."""
        statement = (
            sa.select(
                planned_assignments.c.wbs_node_id,
                planned_assignments.c.employee_id,
                planned_assignments.c.contractor_label,
                planned_assignments.c.start_week,
                planned_assignments.c.end_week,
            )
            .where(planned_assignments.c.run_id == run_id)
            .order_by(planned_assignments.c.start_week, planned_assignments.c.wbs_node_id)
        )
        return [
            PlanRow(
                row.wbs_node_id,
                row.employee_id,
                row.contractor_label,
                row.start_week,
                row.end_week,
            )
            for row in (await self._connection.execute(statement)).all()
        ]

    async def nodes(self, project_id: uuid.UUID) -> list[NodeRow]:
        """The project's WBS nodes with codes, in tree order (labels and groups)."""
        statement = (
            sa.select(
                wbs_nodes_with_code.c.id,
                wbs_nodes_with_code.c.parent_id,
                wbs_nodes_with_code.c.level,
                wbs_nodes_with_code.c.code,
                wbs_nodes_with_code.c.name,
            )
            .where(wbs_nodes_with_code.c.project_id == project_id)
            .order_by(wbs_nodes_with_code.c.path)
        )
        return [
            NodeRow(row.id, row.parent_id, row.level, row.code, row.name)
            for row in (await self._connection.execute(statement)).all()
        ]

    async def employee_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Full names of the given employees (owners in the comparison)."""
        if not ids:
            return {}
        rows = (
            await self._connection.execute(
                sa.select(employees.c.id, employees.c.full_name).where(employees.c.id.in_(ids))
            )
        ).all()
        return {row.id: row.full_name for row in rows}

    # ------------------------------------------------------------------ context
    async def context(
        self, project: ProjectRef, in_force: InForce | None, today: date
    ) -> ChangeContext:
        """Everything a change is read against: budget, deadline, work packages, team,
        dependencies, the skills the project needs, the contractor rate and the plan in force.

        Falls back to the project's own budget and dates when no constraint set exists,
        the same fallbacks the optimizer uses.
        """
        row = (
            await self._connection.execute(
                sa.select(
                    constraint_sets.c.budget_total_cents,
                    constraint_sets.c.must_finish_by,
                    constraint_sets.c.start_date,
                    constraint_sets.c.contractor_rate_cents,
                ).where(constraint_sets.c.project_id == project.id)
            )
        ).first()
        budget = row.budget_total_cents if row and row.budget_total_cents is not None else None
        deadline = row.must_finish_by if row and row.must_finish_by else None
        start = row.start_date if row and row.start_date else None

        nodes = (
            await self._connection.execute(
                sa.select(
                    wbs_nodes_with_code.c.id,
                    wbs_nodes_with_code.c.parent_id,
                    wbs_nodes_with_code.c.level,
                    wbs_nodes_with_code.c.code,
                    wbs_nodes_with_code.c.name,
                    wbs_nodes_with_code.c.duration_days,
                    wbs_nodes_with_code.c.owner_employee_id,
                )
                .where(wbs_nodes_with_code.c.project_id == project.id)
                .order_by(wbs_nodes_with_code.c.path)
            )
        ).all()
        children: dict[uuid.UUID, int] = defaultdict(int)
        for node in nodes:
            if node.parent_id is not None:
                children[node.parent_id] += 1
        tasks = tuple(
            TaskRef(node.id, node.code, node.name, node.duration_days, node.owner_employee_id)
            for node in nodes
            if node.level == 3 or (node.level == 2 and not children.get(node.id))
        )

        edges = tuple(
            (row.predecessor_id, row.successor_id)
            for row in (
                await self._connection.execute(
                    sa.select(dependencies.c.predecessor_id, dependencies.c.successor_id).where(
                        dependencies.c.project_id == project.id
                    )
                )
            ).all()
        )
        return ChangeContext(
            budget_cents=budget if budget is not None else project.budget_cents,
            deadline=deadline or project.target_end_date,
            start=start or project.start_date,
            tasks=tasks,
            people=await self._people(project),
            edges=edges,
            plan_version=in_force.version if in_force else None,
            plan_cost_cents=in_force.expected_cost_cents if in_force else None,
            plan_finish=in_force.completion_date if in_force else None,
            today=today,
            skills=tuple(
                row.name for row in await TeamRepository(self._connection).skill_rows(project.id)
            ),
            contractor_rate_cents=row.contractor_rate_cents if row else None,
        )

    async def _people(self, project: ProjectRef) -> tuple[PersonRef, ...]:
        """The project's team; everyone active in the org when no team is set (as the optimizer)."""
        rows = (
            await self._connection.execute(
                sa.select(
                    employees.c.id, employees.c.full_name, project_team_members.c.hours_per_week
                )
                .select_from(
                    project_team_members.join(
                        employees, employees.c.id == project_team_members.c.employee_id
                    )
                )
                .where(project_team_members.c.project_id == project.id)
                .order_by(employees.c.full_name)
            )
        ).all()
        if not rows:
            rows = (
                await self._connection.execute(
                    sa.select(
                        employees.c.id,
                        employees.c.full_name,
                        employees.c.weekly_capacity_hours.label("hours_per_week"),
                    )
                    .where(employees.c.org_id == project.org_id, employees.c.active.is_(True))
                    .order_by(employees.c.full_name)
                )
            ).all()
        return tuple(PersonRef(row.id, row.full_name, float(row.hours_per_week)) for row in rows)
