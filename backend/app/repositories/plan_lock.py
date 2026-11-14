"""Once a plan is applied the project's planning inputs are frozen (changes go through step 7)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.repositories.tables import optimization_runs, scope_items


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


class PlanLock:
    """The lock as an API dependency, for endpoints whose repository doesn't carry it
    (scope items and intake runs: requirements are inputs too)."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def version(self, project_id: uuid.UUID) -> int | None:
        """The plan version in force on the project (None while nothing is applied)."""
        return await applied_version(self._connection, project_id)

    async def scope_item_version(self, item_id: uuid.UUID) -> int | None:
        """The plan version in force on the scope item's project (None when the item
        isn't visible: the update then answers 404)."""
        project_id = (
            await self._connection.execute(
                sa.select(scope_items.c.project_id).where(scope_items.c.id == item_id)
            )
        ).scalar()
        return None if project_id is None else await applied_version(self._connection, project_id)
