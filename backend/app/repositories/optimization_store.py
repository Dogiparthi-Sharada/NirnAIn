"""Writes the optimization worker makes on its service connection (bypasses RLS).

Purpose: Only this trusted job may write `planned_assignments` and `recommendations` (they have no
user write policies), and it alone moves runs through running, finished and failed.
How it fits: Used by the background runner in app/jobs/optimizer.py; stores the
`app.optimization.explain.Report` of a solve; layer: repositories.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.common import SolverStatus
from app.optimization.explain import Report
from app.repositories.job_rows import started_before
from app.repositories.optimization import ACTIVE
from app.repositories.tables import optimization_runs, planned_assignments, recommendations

runs = optimization_runs


class OptimizationJobStore:
    """Writes made by the background worker on a service connection (bypasses RLS).

    `planned_assignments` and `recommendations` have no user write policies:
    only this trusted job writes them.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the worker's service connection."""
        self._conn = conn

    async def claim(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        """Move a queued run to running; returns its input snapshot (None if it is not queued)."""
        stmt = (
            sa.update(runs)
            .where(runs.c.id == run_id, runs.c.status == SolverStatus.QUEUED)
            .values(status=SolverStatus.RUNNING, started_at=sa.func.now())
            .returning(runs.c.input_snapshot)
        )
        row = (await self._conn.execute(stmt)).first()
        return dict(row.input_snapshot) if row else None

    async def finish(self, run_id: uuid.UUID, report: Report) -> None:
        """Store a solved run: status, KPIs, result, planned assignments and recommendations."""
        await self._conn.execute(
            sa.update(runs)
            .where(runs.c.id == run_id)
            .values(
                status=report.status,
                solver=report.solver,
                solver_version=report.solver_version,
                runtime_ms=report.runtime_ms,
                mip_gap=report.mip_gap,
                objective_value=report.objective_value,
                expected_cost_cents=report.expected_cost_cents,
                completion_date=report.completion_date,
                avg_utilization=report.avg_utilization,
                critical_path=report.critical_path,
                result=report.result,
                infeasibility=report.iis,
                error=report.error,
                finished_at=sa.func.now(),
            )
        )
        if report.assignments:
            await self._conn.execute(
                sa.insert(planned_assignments).values(
                    [
                        {
                            "run_id": run_id,
                            "wbs_node_id": uuid.UUID(assignment.wbs_node_id),
                            "employee_id": uuid.UUID(assignment.employee_id)
                            if assignment.employee_id
                            else None,
                            "contractor_label": assignment.contractor_label,
                            "start_week": assignment.start_week,
                            "end_week": assignment.end_week,
                            "hours_per_week": assignment.hours_per_week,
                            "baseline_start_week": assignment.baseline_start_week,
                            "baseline_end_week": assignment.baseline_end_week,
                            "baseline_employee_id": (
                                uuid.UUID(assignment.baseline_employee_id)
                                if assignment.baseline_employee_id
                                else None
                            ),
                        }
                        for assignment in report.assignments
                    ]
                )
            )
        if report.recommendations:
            await self._conn.execute(
                sa.insert(recommendations).values(
                    [
                        {
                            "run_id": run_id,
                            "type": recommendation.type,
                            "wbs_node_id": uuid.UUID(recommendation.wbs_node_id)
                            if recommendation.wbs_node_id
                            else None,
                            "before_value": recommendation.before_value,
                            "after_value": recommendation.after_value,
                            "reason": recommendation.reason,
                            "sort_order": recommendation.sort_order,
                        }
                        for recommendation in report.recommendations
                    ]
                )
            )

    async def fail(self, run_id: uuid.UUID, error: str) -> None:
        """Mark an active run failed with the error shown to the user."""
        await self._conn.execute(
            sa.update(runs)
            .where(runs.c.id == run_id, runs.c.status.in_(ACTIVE))
            .values(status=SolverStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        """Fail runs active for over `older_than_s` (orphaned by a restart); return the count."""
        result = await self._conn.execute(
            sa.update(runs)
            .where(runs.c.status.in_(ACTIVE), started_before(runs, older_than_s))
            .values(status=SolverStatus.FAILED, error=error, finished_at=sa.func.now())
        )
        return int(result.rowcount)
