"""Once a plan is applied the project's planning inputs are frozen (changes go through step 7)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.repositories.tables import optimization_runs


async def applied_version(conn: AsyncConnection, project_id: uuid.UUID) -> int | None:
    """The latest plan version in force (runs applied before versioning count as v1)."""
    r = optimization_runs
    row = (
        await conn.execute(
            sa.select(
                sa.func.max(r.c.version).label("version"),
                sa.func.bool_or(r.c.applied_at.is_not(None)).label("applied"),
            ).where(r.c.project_id == project_id)
        )
    ).one()
    if row.version is not None:
        return int(row.version)
    return 1 if row.applied else None
