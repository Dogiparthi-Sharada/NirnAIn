"""Repository of projects: paging, reading, creating, updating, deleting and the dashboard KPIs.

Purpose: Persist projects while letting RLS decide what the caller may see and change; a new start
or target end date is mirrored on the step-5 constraint set in the same transaction.
How it fits: Used by app/api/v1/projects.py and app/api/v1/dashboard.py; returns
app/domain/projects.py models; layer: repositories.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.pagination import Cursor
from app.domain.common import ProjectStatus
from app.domain.dashboard import DashboardSummary
from app.domain.project_dates import step5_columns
from app.domain.projects import Project
from app.repositories.tables import constraint_sets, profiles, projects

TIMESTAMP_TZ = sa.DateTime(timezone=True)


def escape_like(value: str) -> str:
    """Escape `%`, `_` and backslashes so user text matches literally in a LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _project_select() -> sa.Select[Any]:
    """Return the project select with the PM's display name joined in."""
    pm = profiles.alias("pm")
    return sa.select(
        *projects.c,
        sa.func.coalesce(pm.c.full_name, pm.c.email).label("pm_name"),
    ).select_from(projects.outerjoin(pm, pm.c.id == projects.c.pm_user_id))


def _to_project(row: sa.Row[Any]) -> Project:
    """Turn a result row into the API model."""
    return Project.model_validate(dict(row._mapping))


class ProjectRepository:
    """Project persistence. Visibility and write permissions come from RLS."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def list_page(
        self,
        org_id: uuid.UUID,
        *,
        statuses: Sequence[ProjectStatus] | None,
        search: str | None,
        limit: int,
        cursor: Cursor | None,
    ) -> tuple[list[Project], Cursor | None]:
        """Return one page of the organization's projects, newest first, and the next page's cursor.

        Archived projects are left out unless `statuses` asks for them; `search` matches name or
        code.
        """
        table = projects
        stmt = _project_select().where(table.c.org_id == org_id)
        if statuses:
            stmt = stmt.where(table.c.status.in_(list(statuses)))
        else:
            stmt = stmt.where(table.c.status != ProjectStatus.ARCHIVED)
        if search:
            pattern = f"%{escape_like(search)}%"
            stmt = stmt.where(
                sa.or_(
                    table.c.name.ilike(pattern, escape="\\"),
                    table.c.code.ilike(pattern, escape="\\"),
                )
            )
        if cursor is not None:
            stmt = stmt.where(
                sa.tuple_(table.c.created_at, table.c.id)
                < sa.tuple_(
                    sa.literal(cursor.created_at, TIMESTAMP_TZ),
                    sa.literal(cursor.id, table.c.id.type),
                )
            )
        stmt = stmt.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(limit + 1)

        rows = (await self._conn.execute(stmt)).all()
        items = [_to_project(row) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and items:
            last = items[-1]
            next_cursor = Cursor(created_at=last.created_at, id=last.id)
        return items, next_cursor

    async def get(self, project_id: uuid.UUID) -> Project | None:
        """Return the project, or None when missing or not visible."""
        stmt = _project_select().where(projects.c.id == project_id)
        row = (await self._conn.execute(stmt)).first()
        return _to_project(row) if row else None

    async def create(
        self, org_id: uuid.UUID, created_by: uuid.UUID, values: dict[str, Any]
    ) -> uuid.UUID:
        """Insert a project and return its new id."""
        # The id is generated here and the row re-selected afterwards instead of
        # using RETURNING, whose visibility depends on SELECT policies.
        project_id = uuid.uuid4()
        stmt = sa.insert(projects).values(
            id=project_id, org_id=org_id, created_by=created_by, **values
        )
        await self._conn.execute(stmt)
        return project_id

    async def update(self, project_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        """Update the given columns; archiving stamps `archived_at`, other statuses clear it.

        A new start or target end date also becomes the step-5 start or must-finish-by date when
        a constraint set exists (a cleared date keeps step 5's). Returns False if no row was
        updated (missing or not writable under RLS).
        """
        values = dict(changes)
        if "status" in values:
            values["archived_at"] = (
                sa.func.coalesce(projects.c.archived_at, sa.func.now())
                if values["status"] == ProjectStatus.ARCHIVED
                else None
            )
        stmt = sa.update(projects).where(projects.c.id == project_id).values(**values)
        result = await self._conn.execute(stmt)
        if result.rowcount == 0:
            return False
        mirrored = step5_columns(changes)
        if mirrored:
            # No constraint set yet: nothing to update, step 5 starts from the project's dates.
            await self._conn.execute(
                sa.update(constraint_sets)
                .where(constraint_sets.c.project_id == project_id)
                .values(**mirrored)
            )
        return True

    async def step5_dates(self, project_id: uuid.UUID) -> tuple[date | None, date | None] | None:
        """Return step 5's saved (start date, must-finish-by), or None before step 5 is saved."""
        row = (
            await self._conn.execute(
                sa.select(constraint_sets.c.start_date, constraint_sets.c.must_finish_by).where(
                    constraint_sets.c.project_id == project_id
                )
            )
        ).first()
        return (row.start_date, row.must_finish_by) if row else None

    async def delete(self, project_id: uuid.UUID) -> bool:
        """Delete the project; False when RLS left nothing to delete."""
        result = await self._conn.execute(sa.delete(projects).where(projects.c.id == project_id))
        return result.rowcount > 0

    async def dashboard(self, org_id: uuid.UUID) -> DashboardSummary:
        """Return the KPI cards over the organization's active (non-archived) projects.

        "Created this month" counts from the start of the current UTC month.
        """
        table = projects
        month_start = sa.func.timezone(
            "UTC", sa.func.date_trunc("month", sa.func.timezone("UTC", sa.func.now()))
        )
        stmt = sa.select(
            sa.func.count().label("total_projects"),
            sa.func.count().filter(table.c.status == ProjectStatus.ON_TRACK).label("on_track"),
            sa.func.count()
            .filter(table.c.status.in_([ProjectStatus.AT_RISK, ProjectStatus.DELAYED]))
            .label("at_risk_or_delayed"),
            sa.cast(sa.func.coalesce(sa.func.sum(table.c.budget_cents), 0), sa.BigInteger).label(
                "total_budget_cents"
            ),
            sa.func.count().filter(table.c.created_at >= month_start).label("created_this_month"),
        ).where(
            table.c.org_id == org_id,
            table.c.status != ProjectStatus.ARCHIVED,
            table.c.archived_at.is_(None),
        )
        row = (await self._conn.execute(stmt)).one()
        return DashboardSummary.model_validate(dict(row._mapping))
