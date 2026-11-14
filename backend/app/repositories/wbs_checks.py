"""What the WBS checks (`app.ai.wbs_validate.check_structure`) read: the WBS, its links,
the dates, the required skills and the skills of the people who would do the work.

Shared by the free `GET …/wbs/checks` (caller's RLS session) and the `wbs_validate` AI
job (service connection, which bypasses RLS), so both check the same data the same way.
Every query names the project explicitly. Rules applied while reading:

* Dates: the constraint set's start and deadline, else the project's own (as the
  optimizer); the caller's `today` moves a past start forward (D4).
* Skill pool: the project team (step 5) when it has members, else every active employee
  of the organization: the people the optimizer plans with (`planning_input.py`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.wbs_validate import DependencyInfo, NodeInfo, ValidationInput
from app.repositories.tables import (
    constraint_sets,
    dependencies,
    employee_skills,
    employees,
    project_skills,
    project_team_members,
    projects,
    skills,
    task_skill_requirements,
    wbs_nodes,
    wbs_nodes_with_code,
)


def _skill_key(name: str) -> str:
    return " ".join(name.split()).casefold()


async def _nodes(conn: AsyncConnection, project_id: uuid.UUID) -> tuple[NodeInfo, ...]:
    tree, people = wbs_nodes_with_code, employees
    stmt = (
        sa.select(
            tree.c.id,
            tree.c.parent_id,
            tree.c.level,
            tree.c.code,
            tree.c.name,
            tree.c.duration_days,
            tree.c.owner_employee_id,
            people.c.full_name.label("owner_name"),
        )
        .select_from(tree.outerjoin(people, people.c.id == tree.c.owner_employee_id))
        .where(tree.c.project_id == project_id)
        .order_by(tree.c.path)
    )
    return tuple(NodeInfo(**row._mapping) for row in (await conn.execute(stmt)).all())


async def _dependencies(conn: AsyncConnection, project_id: uuid.UUID) -> tuple[DependencyInfo, ...]:
    links = dependencies
    stmt = sa.select(
        links.c.predecessor_id, links.c.successor_id, links.c.type, links.c.lag_days
    ).where(links.c.project_id == project_id)
    return tuple(
        DependencyInfo(row.predecessor_id, row.successor_id, row.type, row.lag_days)
        for row in (await conn.execute(stmt)).all()
    )


async def _required_skills(conn: AsyncConnection, project_id: uuid.UUID) -> tuple[str, ...]:
    """Skills a work package is tagged with, plus the project's Required skills."""
    needed, listed, skill, node = task_skill_requirements, project_skills, skills, wbs_nodes
    tagged = (
        sa.select(skill.c.name)
        .select_from(
            needed.join(skill, skill.c.id == needed.c.skill_id).join(
                node, node.c.id == needed.c.wbs_node_id
            )
        )
        .where(node.c.project_id == project_id)
    )
    required = (
        sa.select(skill.c.name)
        .select_from(listed.join(skill, skill.c.id == listed.c.skill_id))
        .where(listed.c.project_id == project_id, listed.c.required.is_(True))
    )
    names: Sequence[Any] = (
        (await conn.execute(sa.union(tagged, required).order_by("name"))).scalars().all()
    )
    return tuple(str(name) for name in names)


async def skill_pool(
    conn: AsyncConnection, project_id: uuid.UUID, org_id: uuid.UUID
) -> tuple[frozenset[str], bool]:
    """(Skills the people who would do the work hold, casefolded; whether that is the team).

    Rule: the project team when it has members, else every active employee of the org, as
    the optimizer plans; a skill counts at any proficiency above 0.
    """
    member = project_team_members
    people: Sequence[Any] = (
        (
            await conn.execute(
                sa.select(member.c.employee_id).where(member.c.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )
    has_team = bool(people)
    if not has_team:
        people = (
            (
                await conn.execute(
                    sa.select(employees.c.id).where(
                        employees.c.org_id == org_id, employees.c.active.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
    if not people:
        return frozenset(), has_team
    held, skill = employee_skills, skills
    stmt = (
        sa.select(skill.c.name)
        .select_from(held.join(skill, skill.c.id == held.c.skill_id))
        .where(held.c.employee_id.in_(people), held.c.proficiency > 0)
        .distinct()
    )
    names: Sequence[Any] = (await conn.execute(stmt)).scalars().all()
    return frozenset(_skill_key(str(name)) for name in names), has_team


async def read_validation_input(
    conn: AsyncConnection, project_id: uuid.UUID, today: date | None
) -> ValidationInput | None:
    """The checks' input for a project (None when it doesn't exist or isn't visible).

    The scope items are left out: only the AI review reads them (`AiJobStore` adds them).
    """
    project, limits = projects, constraint_sets
    row = (
        await conn.execute(
            sa.select(
                project.c.org_id,
                project.c.name,
                sa.func.coalesce(limits.c.start_date, project.c.start_date).label("start_date"),
                sa.func.coalesce(limits.c.must_finish_by, project.c.target_end_date).label(
                    "deadline"
                ),
                sa.func.coalesce(limits.c.working_days_per_week, 5).label("per_week"),
            )
            .select_from(project.outerjoin(limits, limits.c.project_id == project.c.id))
            .where(project.c.id == project_id)
        )
    ).first()
    if row is None:
        return None
    held, has_team = await skill_pool(conn, project_id, row.org_id)
    return ValidationInput(
        project_name=row.name,
        nodes=await _nodes(conn, project_id),
        dependencies=await _dependencies(conn, project_id),
        start_date=row.start_date,
        deadline=row.deadline,
        working_days_per_week=int(row.per_week),
        required_skills=await _required_skills(conn, project_id),
        held_skills=held,
        has_team=has_team,
        today=today,
    )


async def wbs_changed_at(conn: AsyncConnection, project_id: uuid.UUID) -> datetime | None:
    """When the project's WBS nodes last changed (created, edited, moved); None without nodes.

    What the AI review read: the builder compares this with the review's time to say when
    the review is out of date. A deletion leaves no row behind, so it doesn't move it.
    """
    stmt = sa.select(sa.func.max(wbs_nodes.c.updated_at)).where(
        wbs_nodes.c.project_id == project_id
    )
    changed: datetime | None = (await conn.execute(stmt)).scalar()
    return changed
