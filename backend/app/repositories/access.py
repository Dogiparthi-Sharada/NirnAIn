"""Project lookups shared by the planning repositories (all under the caller's RLS session).

Purpose: Answer "can the caller see this project?", "may they change it?" and "is this an active
employee of the organization?" the same way everywhere, letting RLS decide visibility.
How it fits: Called by the team, planning, WBS, intake, document and AI job repositories; reads
app/repositories/tables.py; layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.repositories.tables import employees, projects


@dataclass(frozen=True, slots=True)
class ProjectRef:
    """The project fields the planning repositories need (dates, budget, organization)."""

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    start_date: date | None
    target_end_date: date | None
    budget_cents: int | None


async def visible_project(conn: AsyncConnection, project_id: uuid.UUID) -> ProjectRef | None:
    """Return the project when the caller can see it (RLS hides the others), else None."""
    stmt = sa.select(
        projects.c.id,
        projects.c.org_id,
        projects.c.name,
        projects.c.start_date,
        projects.c.target_end_date,
        projects.c.budget_cents,
    ).where(projects.c.id == project_id)
    row = (await conn.execute(stmt)).first()
    return ProjectRef(**row._mapping) if row else None


async def can_write_project(conn: AsyncConnection, project_id: uuid.UUID) -> bool:
    """Tell whether the caller may change the project (the RLS rule `can_write_project`)."""
    stmt = sa.select(sa.func.private.can_write_project(project_id))
    return bool((await conn.execute(stmt)).scalar())


async def is_active_employee(
    conn: AsyncConnection, org_id: uuid.UUID, employee_id: uuid.UUID
) -> bool:
    """Tell whether the employee is an active member of the organization's staff."""
    stmt = sa.select(employees.c.id).where(
        employees.c.id == employee_id, employees.c.org_id == org_id, employees.c.active.is_(True)
    )
    return (await conn.execute(stmt)).first() is not None
