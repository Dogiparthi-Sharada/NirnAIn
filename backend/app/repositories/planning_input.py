"""Read a project's planning data and freeze it into the optimizer's input.

Runs under the caller's RLS session (`OptimizationRepository` delegates
here), so a user can only plan with data they can see. Business rules
applied while reading (approved NirnAIn workflow, step 5):

* Capacity: when the project has a team (`project_team_members`) only those
  people plan, at the hours per week they give this project; otherwise every
  active employee of the organization, at their default weekly capacity.
* Deadline policy follows the PM's answer: 'soft' -> can move, anything else
  (hard, Don't know, not answered) -> hard.
* Outside specialists only when the PM answered 'yes', at the contractor
  rate or the $175/h estimate.
* Planning starts today: unfinished work never starts before `today` (the
  caller's date, injectable for tests), even when the start date has passed.
* Hourly rates are read as stored; people without one are planned at the
  $85/h default (`model.DEFAULT_HOURLY_RATE_CENTS`) and the confirmation
  sheet says so.
* Budget, capacity and skill policies are used as stored.

`input_facts.py` gathers what the confirmation sheet needs beyond the input.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.optimization.model import (
    Allocation,
    Constraints,
    Dependency,
    Employee,
    Milestone,
    PlanningInput,
    SkillRequirement,
    Task,
)
from app.optimization.remedies import DEFAULT_SPECIALIST_RATE_CENTS
from app.repositories.tables import (
    allocations,
    constraint_sets,
    dependencies,
    employee_skills,
    employees,
    milestones,
    project_team_members,
    skills,
    task_skill_requirements,
    wbs_nodes_with_code,
)

DELIVERABLE_LEVEL = 2


@dataclass(frozen=True, slots=True)
class ProjectContext:
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    start_date: date | None
    target_end_date: date | None
    budget_cents: int | None


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_constraints(
    constraint_set: sa.Row[Any] | None, project: ProjectContext, today: date
) -> tuple[Constraints, date]:
    """Constraint set with fallbacks to the project's own dates and budget; and the start."""
    start = (constraint_set.start_date if constraint_set else None) or project.start_date or today
    if constraint_set is None:
        # Nothing answered yet: hard deadline, no outside specialists.
        return (
            Constraints(
                budget_total_cents=project.budget_cents,
                must_finish_by=project.target_end_date,
                allow_contractors=False,
            ),
            start,
        )
    specialists_allowed = constraint_set.specialist_answer == "yes"
    rate = constraint_set.contractor_rate_cents
    if specialists_allowed and rate is None:
        rate = DEFAULT_SPECIALIST_RATE_CENTS
    budget = constraint_set.budget_total_cents
    return (
        Constraints(
            budget_total_cents=budget if budget is not None else project.budget_cents,
            labor_cap_cents=constraint_set.labor_cap_cents,
            budget_mode=str(constraint_set.budget_mode),  # type: ignore[arg-type]
            budget_penalty=float(constraint_set.budget_penalty),
            must_finish_by=constraint_set.must_finish_by or project.target_end_date,
            deadline_mode="soft" if constraint_set.deadline_answer == "soft" else "hard",
            deadline_penalty_per_day=float(constraint_set.deadline_penalty_per_day),
            working_days_per_week=int(constraint_set.working_days_per_week),
            capacity_mode=str(constraint_set.capacity_mode),  # type: ignore[arg-type]
            capacity_penalty=float(constraint_set.capacity_penalty),
            skill_mode=str(constraint_set.skill_mode),  # type: ignore[arg-type]
            allow_contractors=specialists_allowed,
            contractor_rate_cents=rate,
        ),
        start,
    )


async def read_constraint_set(conn: AsyncConnection, project_id: uuid.UUID) -> sa.Row[Any] | None:
    """The project's constraint set row (None until step 5 is saved)."""
    return (
        await conn.execute(
            sa.select(constraint_sets).where(constraint_sets.c.project_id == project_id)
        )
    ).first()


class PlanningInputReader:
    """Queries behind `load_input` and the confirmation sheet."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def load(self, project: ProjectContext, today: date) -> PlanningInput:
        """Read the planning data the caller can see and freeze it into a model input.

        Work packages are level-3 nodes and level-2 nodes without children,
        with a positive duration. Dependencies and milestones on summary
        nodes are expanded to the work packages underneath them.
        """
        project_id = project.id
        view = wbs_nodes_with_code
        nodes = (
            await self._conn.execute(
                sa.select(
                    view.c.id,
                    view.c.parent_id,
                    view.c.level,
                    view.c.name,
                    view.c.duration_days,
                    view.c.effort_hours,
                    view.c.owner_employee_id,
                    view.c.code,
                )
                .where(view.c.project_id == project_id)
                .order_by(view.c.path)
            )
        ).all()
        children: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for node in nodes:
            if node.parent_id is not None:
                children[node.parent_id].append(node.id)
        work = [
            node
            for node in nodes
            if (node.level == 3 or (node.level == DELIVERABLE_LEVEL and not children.get(node.id)))
            and (node.duration_days or 0) > 0
        ]
        work_ids = {node.id for node in work}

        def leaves(node_id: uuid.UUID) -> list[uuid.UUID]:
            if node_id in work_ids:
                return [node_id]
            return [leaf for child in children.get(node_id, []) for leaf in leaves(child)]

        requirements = await self._requirements(work_ids)
        team, proficiencies = await self._team(project)

        dependency_rows = (
            await self._conn.execute(
                sa.select(
                    dependencies.c.predecessor_id,
                    dependencies.c.successor_id,
                    dependencies.c.type,
                    dependencies.c.lag_days,
                )
                .where(dependencies.c.project_id == project_id)
                .order_by(dependencies.c.predecessor_id, dependencies.c.successor_id)
            )
        ).all()
        links: dict[tuple[str, str], Dependency] = {}
        for row in dependency_rows:
            for predecessor in leaves(row.predecessor_id):
                for successor in leaves(row.successor_id):
                    if predecessor != successor:
                        links.setdefault(
                            (str(predecessor), str(successor)),
                            Dependency(
                                str(predecessor),
                                str(successor),
                                str(row.type),  # type: ignore[arg-type]
                                int(row.lag_days),
                            ),
                        )

        constraint_set = await read_constraint_set(self._conn, project_id)
        constraints, start = build_constraints(constraint_set, project, today)

        milestone_list = []
        for row in (
            await self._conn.execute(
                sa.select(
                    milestones.c.id,
                    milestones.c.name,
                    milestones.c.due_date,
                    milestones.c.wbs_node_id,
                    milestones.c.mode,
                )
                .where(milestones.c.project_id == project_id)
                .order_by(milestones.c.due_date, milestones.c.id)
            )
        ).all():
            covered = leaves(row.wbs_node_id) if row.wbs_node_id is not None else []
            # A milestone without work packages under it has nothing to check.
            if covered:
                milestone_list.append(
                    Milestone(
                        id=str(row.id),
                        name=row.name,
                        due_date=row.due_date,
                        mode=str(row.mode),  # type: ignore[arg-type]
                        task_ids=tuple(str(task_id) for task_id in covered),
                    )
                )

        bookings = await self._allocations([row.id for row in team], project_id, start)

        return PlanningInput(
            project_id=str(project_id),
            project_name=project.name,
            start_date=start,
            tasks=tuple(
                Task(
                    id=str(node.id),
                    code=node.code,
                    name=node.name,
                    duration_days=int(node.duration_days),
                    effort_hours=None if node.effort_hours is None else float(node.effort_hours),
                    owner_id=(
                        None if node.owner_employee_id is None else str(node.owner_employee_id)
                    ),
                    skills=tuple(requirements.get(node.id, ())),
                )
                for node in work
            ),
            employees=tuple(
                Employee(
                    id=str(row.id),
                    name=row.full_name,
                    weekly_capacity_hours=float(row.hours_per_week),
                    hourly_rate_cents=row.hourly_rate_cents,
                    is_contractor=bool(row.is_contractor),
                    skills=proficiencies.get(row.id, {}),
                )
                for row in team
            ),
            dependencies=tuple(links.values()),
            constraints=constraints,
            milestones=tuple(milestone_list),
            allocations=tuple(bookings),
            deliverables={
                node.code: node.name for node in nodes if node.level == DELIVERABLE_LEVEL
            },
            today=today,
        )

    async def _requirements(
        self, work_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, list[SkillRequirement]]:
        requirements: dict[uuid.UUID, list[SkillRequirement]] = defaultdict(list)
        if not work_ids:
            return requirements
        required = task_skill_requirements
        rows = (
            await self._conn.execute(
                sa.select(
                    required.c.wbs_node_id,
                    required.c.skill_id,
                    skills.c.name,
                    required.c.min_proficiency,
                )
                .select_from(required.join(skills, skills.c.id == required.c.skill_id))
                .where(required.c.wbs_node_id.in_(work_ids))
                .order_by(required.c.wbs_node_id, skills.c.name)
            )
        ).all()
        for row in rows:
            requirements[row.wbs_node_id].append(
                SkillRequirement(str(row.skill_id), str(row.name), int(row.min_proficiency))
            )
        return requirements

    async def _team(
        self, project: ProjectContext
    ) -> tuple[Sequence[sa.Row[Any]], dict[uuid.UUID, dict[str, int]]]:
        """The project team at its hours on this project, else every active employee."""
        member = project_team_members
        columns = (
            employees.c.id,
            employees.c.full_name,
            employees.c.hourly_rate_cents,
            employees.c.is_contractor,
        )
        team = (
            await self._conn.execute(
                sa.select(*columns, member.c.hours_per_week)
                .select_from(member.join(employees, employees.c.id == member.c.employee_id))
                .where(member.c.project_id == project.id)
                .order_by(employees.c.full_name, employees.c.id)
            )
        ).all()
        if not team:
            team = (
                await self._conn.execute(
                    sa.select(*columns, employees.c.weekly_capacity_hours.label("hours_per_week"))
                    .where(employees.c.org_id == project.org_id, employees.c.active.is_(True))
                    .order_by(employees.c.full_name, employees.c.id)
                )
            ).all()
        proficiencies: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
        employee_ids = [row.id for row in team]
        if employee_ids:
            for row in (
                await self._conn.execute(
                    sa.select(
                        employee_skills.c.employee_id,
                        employee_skills.c.skill_id,
                        employee_skills.c.proficiency,
                    ).where(employee_skills.c.employee_id.in_(employee_ids))
                )
            ).all():
                proficiencies[row.employee_id][str(row.skill_id)] = int(row.proficiency)
        return team, proficiencies

    async def _allocations(
        self, employee_ids: list[uuid.UUID], project_id: uuid.UUID, start: date
    ) -> list[Allocation]:
        """Hours the team is already booked on other projects from the start week on."""
        if not employee_ids:
            return []
        rows = (
            await self._conn.execute(
                sa.select(allocations.c.employee_id, allocations.c.week_start, allocations.c.hours)
                .where(
                    allocations.c.employee_id.in_(employee_ids),
                    allocations.c.project_id != project_id,
                    allocations.c.week_start >= _monday(start),
                )
                .order_by(allocations.c.employee_id, allocations.c.week_start)
            )
        ).all()
        return [Allocation(str(row.employee_id), row.week_start, float(row.hours)) for row in rows]
