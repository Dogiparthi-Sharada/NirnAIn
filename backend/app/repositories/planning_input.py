"""Read a project's planning data and freeze it into the optimizer's input.

Purpose: A run must plan with exactly the data the caller can see, frozen at queue time, so the
approved step-5 business rules are applied while reading (see `PlanningInputReader`).
How it fits: Runs under the caller's RLS session (`OptimizationRepository.load_input` and the
step-7 re-plan delegate here); constraints come from app/repositories/constraint_input.py and
`input_facts.py` gathers what the confirmation sheet needs beyond the input; layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.optimization.model import (
    Allocation,
    Dependency,
    Employee,
    Milestone,
    PlanningInput,
    SkillRequirement,
    Task,
)
from app.optimization.skill_links import PackageText, ProjectSkill, link_skills
from app.repositories.access import ProjectRef
from app.repositories.constraint_input import build_constraints, read_constraint_set
from app.repositories.finished_work import finished_work
from app.repositories.tables import (
    allocations,
    dependencies,
    employee_skills,
    employees,
    milestones,
    project_skills,
    project_team_members,
    skills,
    task_skill_requirements,
    wbs_nodes_with_code,
)
from app.repositories.work_packages import DELIVERABLE_LEVEL, work_packages

# The project fields the optimizer input needs: the same lookup as `access.visible_project`.
ProjectContext = ProjectRef


def _monday(day: date) -> date:
    """Return the Monday of the week the day falls in."""
    return day - timedelta(days=day.weekday())


class _WorkTree:
    """The WBS's parent/child links and which nodes are plannable work packages."""

    def __init__(self, nodes: Sequence[sa.Row[Any]], work_ids: set[uuid.UUID]) -> None:
        """Index the children of every node and remember the work package ids."""
        self._children: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for node in nodes:
            if node.parent_id is not None:
                self._children[node.parent_id].append(node.id)
        self._work_ids = work_ids

    def leaves(self, node_id: uuid.UUID) -> list[uuid.UUID]:
        """Return the work packages at or under a node, in WBS order.

        Links and milestones set on a summary node apply to every package beneath it.
        """
        if node_id in self._work_ids:
            return [node_id]
        return [leaf for child in self._children.get(node_id, []) for leaf in self.leaves(child)]


class PlanningInputReader:
    """Queries behind `load_input` and the confirmation sheet.

    Business rules applied while reading (approved NirnAIn workflow, step 5):
    capacity: with a project team only those people plan, at the hours they give this project;
    otherwise every active employee at their default weekly capacity. Skills: the project's
    Required skills reach the packages they are linked to, by WBS tag or else by keyword
    (`optimization/skill_links.py`). Planning starts today: unfinished work never starts before
    `today`. Hourly rates are read as stored; people without one are planned at the $85/h default.
    Budget, capacity and skill policies are used as stored, the contingency reserve held back from
    the budget. Done work stays done (round 3, R2): a 0-day package an applied plan scheduled is
    loaded as done at its applied dates and needs no skills (`finished_work.py`).
    """

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def load(self, project: ProjectContext, today: date) -> PlanningInput:
        """Read the planning data the caller can see and freeze it into a model input.

        Work packages are level-3 nodes and level-2 nodes without children, with a positive
        duration, plus finished work (0 days left, scheduled by an applied plan). The others block
        the run (`work_packages.packages_without_duration`).
        """
        project_id = project.id
        nodes = await self._wbs_nodes(project_id)
        packages = work_packages(nodes)
        finished = await finished_work(
            self._conn, project_id, [node.id for node in packages if node.duration_days == 0]
        )
        work = [node for node in packages if (node.duration_days or 0) > 0 or node.id in finished]
        tree = _WorkTree(nodes, {node.id for node in work})
        requirements = await self._requirements(
            project_id, [node for node in work if node.id not in finished]
        )
        team, proficiencies = await self._team(project)
        links = await self._dependencies(project_id, tree)
        constraint_set = await read_constraint_set(self._conn, project_id)
        constraints, start = build_constraints(constraint_set, project, today)
        milestone_list = await self._milestones(project_id, tree)
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
                    skills=tuple(requirements.get(str(node.id), ())),
                    done=node.id in finished,
                    applied_start=finished[node.id][0] if node.id in finished else None,
                    applied_end=finished[node.id][1] if node.id in finished else None,
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
            dependencies=tuple(links),
            constraints=constraints,
            milestones=tuple(milestone_list),
            allocations=tuple(bookings),
            deliverables={
                node.code: node.name for node in nodes if node.level == DELIVERABLE_LEVEL
            },
            today=today,
        )

    async def _wbs_nodes(self, project_id: uuid.UUID) -> Sequence[sa.Row[Any]]:
        """Return the project's WBS nodes in WBS order."""
        view = wbs_nodes_with_code
        return (
            await self._conn.execute(
                sa.select(
                    view.c.id,
                    view.c.parent_id,
                    view.c.level,
                    view.c.name,
                    view.c.description,
                    view.c.duration_days,
                    view.c.effort_hours,
                    view.c.owner_employee_id,
                    view.c.code,
                )
                .where(view.c.project_id == project_id)
                .order_by(view.c.path)
            )
        ).all()

    async def _dependencies(self, project_id: uuid.UUID, tree: _WorkTree) -> list[Dependency]:
        """Return the project's links expanded to work packages, without duplicates or self-links.

        A link between summary nodes links every package under one to every package under the
        other; the first link found for a pair wins.
        """
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
            for predecessor in tree.leaves(row.predecessor_id):
                for successor in tree.leaves(row.successor_id):
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
        return list(links.values())

    async def _milestones(self, project_id: uuid.UUID, tree: _WorkTree) -> list[Milestone]:
        """Return the milestones that cover work packages, by due date."""
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
            covered = tree.leaves(row.wbs_node_id) if row.wbs_node_id is not None else []
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
        return milestone_list

    async def _requirements(
        self, project_id: uuid.UUID, work: Sequence[sa.Row[Any]]
    ) -> dict[str, list[SkillRequirement]]:
        """Return the skills each work package needs, by package id.

        Skills tagged on step 4 (never an Optional one), plus the project's Required skills
        linked by keyword (`skill_links.link_skills`).
        """
        if not work:
            return {}
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
                .where(required.c.wbs_node_id.in_([node.id for node in work]))
                .order_by(required.c.wbs_node_id, skills.c.name)
            )
        ).all()
        tagged: dict[str, list[SkillRequirement]] = defaultdict(list)
        for row in rows:
            tagged[str(row.wbs_node_id)].append(
                SkillRequirement(str(row.skill_id), str(row.name), int(row.min_proficiency))
            )
        listed = (
            await self._conn.execute(
                sa.select(project_skills.c.skill_id, skills.c.name, project_skills.c.required)
                .select_from(project_skills.join(skills, skills.c.id == project_skills.c.skill_id))
                .where(project_skills.c.project_id == project_id)
                .order_by(skills.c.name)
            )
        ).all()
        return link_skills(
            [PackageText(str(node.id), node.name, node.description) for node in work],
            [ProjectSkill(str(row.skill_id), str(row.name), bool(row.required)) for row in listed],
            tagged,
        )

    async def _team(
        self, project: ProjectContext
    ) -> tuple[Sequence[sa.Row[Any]], dict[uuid.UUID, dict[str, int]]]:
        """Return the people who plan and their skill proficiencies.

        The project team at its hours on this project, else every active employee at their
        default weekly capacity.
        """
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
        """Return the hours the team is already booked on other projects from the start week."""
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
