"""Project lookups shared by the planning repositories (all under the caller's RLS session)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.repositories.tables import employees, projects


@dataclass(frozen=True, slots=True)
class ProjectRef:
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    start_date: date | None
    target_end_date: date | None
    budget_cents: int | None


async def visible_project(conn: AsyncConnection, project_id: uuid.UUID) -> ProjectRef | None:
    p = projects
    stmt = sa.select(
        p.c.id, p.c.org_id, p.c.name, p.c.start_date, p.c.target_end_date, p.c.budget_cents
    ).where(p.c.id == project_id)
    row = (await conn.execute(stmt)).first()
    return ProjectRef(**row._mapping) if row else None


async def can_write_project(conn: AsyncConnection, project_id: uuid.UUID) -> bool:
    stmt = sa.select(sa.func.private.can_write_project(project_id))
    return bool((await conn.execute(stmt)).scalar())


async def is_active_employee(
    conn: AsyncConnection, org_id: uuid.UUID, employee_id: uuid.UUID
) -> bool:
    e = employees
    stmt = sa.select(e.c.id).where(
        e.c.id == employee_id, e.c.org_id == org_id, e.c.active.is_(True)
    )
    return (await conn.execute(stmt)).first() is not None
