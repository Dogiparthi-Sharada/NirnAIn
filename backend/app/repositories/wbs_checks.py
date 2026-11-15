"""What the built-in WBS checks read: the WBS, its links, the dates, needed skills and the team.

Purpose: The free `GET …/wbs/checks` (caller's RLS session) and the `wbs_validate` AI job (service
connection) must check the same data the same way, so every query names the project explicitly.
Dates: the constraint set's start and deadline, else the project's own, with `today` moving a past
start forward (D4). People: only the project team; until step 5 sets one, step 4 names the skills
the WBS needs instead of checking them. Specialists: allowed when step 5 answered 'yes' (as
`constraint_input.build_constraints` reads it); their rate counts only once confirmed, the $175/h
estimate when no amount is stored.
How it fits: Called by app/repositories/wbs.py and app/repositories/ai_jobs.py; feeds
`app.ai.wbs_validate.check_structure`; layer: repositories.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.wbs_validate import DependencyInfo, NodeInfo, ValidationInput
from app.optimization.remedy_options import DEFAULT_SPECIALIST_RATE_CENTS
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
    """Return a skill name normalised for comparison (single spaces, casefolded)."""
    return " ".join(name.split()).casefold()


async def _nodes(conn: AsyncConnection, project_id: uuid.UUID) -> tuple[NodeInfo, ...]:
    """Return the project's WBS nodes in tree order, with owner names."""
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
    """Return the project's dependency links."""
    links = dependencies
    stmt = sa.select(
        links.c.predecessor_id, links.c.successor_id, links.c.type, links.c.lag_days
    ).where(links.c.project_id == project_id)
    return tuple(
        DependencyInfo(row.predecessor_id, row.successor_id, row.type, row.lag_days)
        for row in (await conn.execute(stmt)).all()
    )


async def _required_skills(conn: AsyncConnection, project_id: uuid.UUID) -> tuple[str, ...]:
    """Return the skills work packages are tagged with plus the project's Required skills."""
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


async def project_team(
    conn: AsyncConnection, project_id: uuid.UUID
) -> tuple[frozenset[uuid.UUID], frozenset[str]]:
    """Return the project team's employee ids and the skills they hold (casefolded).

    Both are empty until step 5 sets a team; a skill counts at any proficiency above 0.
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
    if not people:
        return frozenset(), frozenset()
    held, skill = employee_skills, skills
    stmt = (
        sa.select(skill.c.name)
        .select_from(held.join(skill, skill.c.id == held.c.skill_id))
        .where(held.c.employee_id.in_(people), held.c.proficiency > 0)
        .distinct()
    )
    names: Sequence[Any] = (await conn.execute(stmt)).scalars().all()
    return frozenset(people), frozenset(_skill_key(str(name)) for name in names)


async def read_validation_input(
    conn: AsyncConnection, project_id: uuid.UUID, today: date | None
) -> ValidationInput | None:
    """Return the checks' input for a project (None when it doesn't exist or isn't visible).

    The scope items are left out: only the AI review reads them (`AiJobStore` adds them).
    """
    project, limits = projects, constraint_sets
    row = (
        await conn.execute(
            sa.select(
                project.c.name,
                sa.func.coalesce(limits.c.start_date, project.c.start_date).label("start_date"),
                sa.func.coalesce(limits.c.must_finish_by, project.c.target_end_date).label(
                    "deadline"
                ),
                sa.func.coalesce(limits.c.working_days_per_week, 5).label("per_week"),
                limits.c.specialist_answer,
                limits.c.specialist_rate_confirmed,
                limits.c.contractor_rate_cents,
            )
            .select_from(project.outerjoin(limits, limits.c.project_id == project.c.id))
            .where(project.c.id == project_id)
        )
    ).first()
    if row is None:
        return None
    team_ids, held = await project_team(conn, project_id)
    return ValidationInput(
        project_name=row.name,
        nodes=await _nodes(conn, project_id),
        dependencies=await _dependencies(conn, project_id),
        start_date=row.start_date,
        deadline=row.deadline,
        working_days_per_week=int(row.per_week),
        specialists_allowed=row.specialist_answer == "yes",
        specialist_rate_cents=(
            row.contractor_rate_cents or DEFAULT_SPECIALIST_RATE_CENTS
            if row.specialist_rate_confirmed
            else None
        ),
        required_skills=await _required_skills(conn, project_id),
        held_skills=held,
        team_ids=team_ids,
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
