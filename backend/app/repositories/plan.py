"""Plan versions, applying a run and export data (caller's RLS session)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.plan import PlanVersion
from app.export.model import PlannedRow, RunHeader
from app.repositories.tables import (
    employees,
    optimization_runs,
    planned_assignments,
    projects,
    wbs_nodes_with_code,
)

r = optimization_runs


class PlanRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project_visible(self, project_id: uuid.UUID) -> bool:
        stmt = sa.select(projects.c.id).where(projects.c.id == project_id)
        return (await self._conn.execute(stmt)).first() is not None

    async def versions(self, project_id: uuid.UUID) -> list[PlanVersion]:
        stmt = (
            sa.select(
                r.c.version,
                r.c.id,
                r.c.applied_at,
                r.c.completion_date,
                r.c.expected_cost_cents,
                r.c.solver,
            )
            .where(r.c.project_id == project_id, r.c.version.is_not(None))
            .order_by(r.c.version)
        )
        return [
            PlanVersion(
                version=row.version,
                run_id=row.id,
                applied_at=row.applied_at,
                completion_date=row.completion_date,
                expected_cost_cents=row.expected_cost_cents,
                solver=row.solver,
                label=f"Plan v{row.version}",
            )
            for row in (await self._conn.execute(stmt)).all()
        ]

    async def apply(self, run_id: uuid.UUID) -> None:
        """Runs `public.apply_optimization_run`, which checks the caller can write the project."""
        await self._conn.execute(sa.select(sa.func.public.apply_optimization_run(run_id)))

    async def export_header(self, project_id: uuid.UUID, run_id: uuid.UUID) -> RunHeader | None:
        p = projects
        stmt = (
            sa.select(
                r.c.id,
                p.c.name,
                r.c.status,
                r.c.solver,
                r.c.version,
                r.c.applied_at,
                r.c.completion_date,
                r.c.expected_cost_cents,
                r.c.result,
                r.c.input_snapshot,
                r.c.created_at,
            )
            .select_from(r.join(p, p.c.id == r.c.project_id))
            .where(r.c.project_id == project_id, r.c.id == run_id)
        )
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        return RunHeader(
            id=row.id,
            project_name=row.name,
            status=str(row.status),
            solver=row.solver,
            version=row.version,
            applied_at=row.applied_at,
            completion_date=row.completion_date,
            expected_cost_cents=row.expected_cost_cents,
            result=row.result,
            snapshot=row.input_snapshot or {},
            created_at=row.created_at,
        )

    async def export_rows(self, run_id: uuid.UUID) -> list[PlannedRow]:
        pa, w, e = planned_assignments, wbs_nodes_with_code, employees
        stmt = (
            sa.select(
                pa.c.wbs_node_id,
                w.c.code,
                w.c.name,
                w.c.duration_days,
                pa.c.employee_id,
                e.c.full_name,
                pa.c.contractor_label,
                pa.c.start_week,
                pa.c.end_week,
                pa.c.hours_per_week,
            )
            .select_from(
                pa.join(w, w.c.id == pa.c.wbs_node_id).outerjoin(e, e.c.id == pa.c.employee_id)
            )
            .where(pa.c.run_id == run_id)
            .order_by(w.c.path, pa.c.start_week)
        )
        return [
            PlannedRow(
                wbs_node_id=row.wbs_node_id,
                code=row.code,
                name=row.name,
                duration_days=row.duration_days,
                employee_id=row.employee_id,
                employee_name=row.full_name,
                contractor_label=row.contractor_label,
                start_week=row.start_week,
                end_week=row.end_week,
                hours_per_week=float(row.hours_per_week),
            )
            for row in (await self._conn.execute(stmt)).all()
        ]
