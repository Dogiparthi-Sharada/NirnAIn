"""Optimization runs: user-side queries (RLS) and the worker's service-side writes.

Reading the planning data itself lives in `planning_input.py`; this module
creates and reads runs, links re-plans to change requests and stores what
the background worker produced.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import pg_error_info
from app.domain.common import SolverStatus
from app.domain.optimization import OptimizationRun
from app.optimization.explain import Report
from app.optimization.inputs import InputFacts
from app.optimization.model import PlanningInput
from app.repositories.input_facts import InputFactsReader
from app.repositories.planning_input import PlanningInputReader, ProjectContext
from app.repositories.tables import (
    change_requests,
    optimization_runs,
    planned_assignments,
    projects,
    recommendations,
)
from app.repositories.work_packages import packages_without_duration

__all__ = [
    "ActiveRun",
    "ActiveRunExistsError",
    "AppliedRun",
    "OptimizationJobStore",
    "OptimizationRepository",
    "ProjectContext",
]

ACTIVE = (SolverStatus.QUEUED, SolverStatus.RUNNING)
r = optimization_runs


class ActiveRunExistsError(Exception):
    """The one-active-run-per-project unique index rejected the insert."""


@dataclass(frozen=True, slots=True)
class ActiveRun:
    id: uuid.UUID
    stale: bool


@dataclass(frozen=True, slots=True)
class AppliedRun:
    """The plan in force and the inputs hash it was solved from (null for older runs)."""

    id: uuid.UUID
    inputs_hash: str | None


def _run_select() -> sa.Select[Any]:
    return sa.select(
        r.c.id,
        r.c.project_id,
        r.c.status,
        r.c.solver,
        r.c.runtime_ms,
        r.c.error,
        r.c.infeasibility,
        r.c.result,
        r.c.created_at,
        r.c.started_at,
        r.c.finished_at,
        r.c.version,
        r.c.applied_at,
        r.c.options,
    )


def _to_run(row: sa.Row[Any]) -> OptimizationRun:
    m = row._mapping
    return OptimizationRun.model_validate(
        {
            "id": m["id"],
            "project_id": m["project_id"],
            "status": m["status"],
            "solver": m["solver"],
            "solve_seconds": None if m["runtime_ms"] is None else m["runtime_ms"] / 1000,
            "error": m["error"],
            "iis": m["infeasibility"],
            "result": m["result"],
            "created_at": m["created_at"],
            "started_at": m["started_at"],
            "finished_at": m["finished_at"],
            "version": m.get("version"),
            "applied_at": m.get("applied_at"),
            "options": m.get("options") or {},
        }
    )


def _older_than(seconds: int) -> sa.ColumnElement[bool]:
    cutoff = sa.func.now() - sa.func.make_interval(0, 0, 0, 0, 0, 0, seconds)
    return sa.func.coalesce(r.c.started_at, r.c.created_at) < cutoff


class OptimizationRepository:
    """User-facing queries: everything runs under the caller's RLS session."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectContext | None:
        p = projects
        stmt = sa.select(
            p.c.id, p.c.org_id, p.c.name, p.c.start_date, p.c.target_end_date, p.c.budget_cents
        ).where(p.c.id == project_id)
        row = (await self._conn.execute(stmt)).first()
        return ProjectContext(**row._mapping) if row else None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        stmt = sa.select(sa.func.private.can_write_project(project_id))
        return bool((await self._conn.execute(stmt)).scalar())

    async def lock_run_creation(self, project_id: uuid.UUID) -> bool:
        """Claim the right to start the project's next run until this transaction ends.

        Business rule (NFR-005): one run at a time, race-proof. Run creation checks for an
        active run, loads the inputs, then inserts; a second request (a double-click) that
        checked before the first committed could insert after the first run had already
        finished. With the claim it is refused at once instead (False: someone holds it).
        """
        key = sa.func.hashtextextended(f"optimization_runs:{project_id}", 0)
        claimed = await self._conn.execute(sa.select(sa.func.pg_try_advisory_xact_lock(key)))
        return bool(claimed.scalar())

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        stmt = (
            sa.select(r.c.id, _older_than(stale_after_s).label("stale"))
            .where(r.c.project_id == project_id, r.c.status.in_(ACTIVE))
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return ActiveRun(id=row.id, stale=bool(row.stale)) if row else None

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        await self._conn.execute(
            sa.update(r)
            .where(r.c.id == run_id, r.c.status.in_(ACTIVE))
            .values(status=SolverStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def create(
        self,
        project_id: uuid.UUID,
        snapshot: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        # Id generated here and the row re-selected, as for projects (RETURNING
        # visibility depends on SELECT policies).
        run_id = uuid.uuid4()
        try:
            await self._conn.execute(
                sa.insert(r).values(
                    id=run_id,
                    project_id=project_id,
                    input_snapshot=snapshot,
                    options=options or {},
                )
            )
        except IntegrityError as exc:
            if pg_error_info(exc).sqlstate == "23505":
                raise ActiveRunExistsError from exc
            raise
        return run_id

    async def get(self, project_id: uuid.UUID, run_id: uuid.UUID) -> OptimizationRun | None:
        stmt = _run_select().where(r.c.project_id == project_id, r.c.id == run_id)
        row = (await self._conn.execute(stmt)).first()
        return _to_run(row) if row else None

    async def latest(self, project_id: uuid.UUID) -> OptimizationRun | None:
        stmt = (
            _run_select()
            .where(r.c.project_id == project_id)
            .order_by(r.c.created_at.desc(), r.c.id.desc())
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return _to_run(row) if row else None

    # ------------------------------------------------------------------ input
    async def load_input(self, project: ProjectContext, today: date) -> PlanningInput:
        """The project's planning data, frozen into the optimizer's input (see planning_input)."""
        return await PlanningInputReader(self._conn).load(project, today)

    async def input_facts(self, project: ProjectContext, inp: PlanningInput) -> InputFacts:
        """What the confirmation sheet shows beyond `inp` (answers, confirmations, sources)."""
        return await InputFactsReader(self._conn).facts(project, inp)

    async def packages_without_duration(self, project_id: uuid.UUID) -> list[str]:
        """'code name' of the work packages the optimizer can't plan (see work_packages)."""
        return await packages_without_duration(self._conn, project_id)

    async def baseline_owners(
        self, project_id: uuid.UUID, run_id: uuid.UUID
    ) -> dict[str, str] | None:
        """WBS node id -> employee id of an earlier run's plan (None: no such run)."""
        found = (
            await self._conn.execute(
                sa.select(optimization_runs.c.id).where(
                    optimization_runs.c.project_id == project_id, optimization_runs.c.id == run_id
                )
            )
        ).first()
        if found is None:
            return None
        planned = planned_assignments
        rows = (
            await self._conn.execute(
                sa.select(planned.c.wbs_node_id, planned.c.employee_id).where(
                    planned.c.run_id == run_id, planned.c.employee_id.is_not(None)
                )
            )
        ).all()
        return {str(row.wbs_node_id): str(row.employee_id) for row in rows}

    async def applied_run(self, project_id: uuid.UUID) -> AppliedRun | None:
        """The plan in force: the run applied last (None while nothing is applied)."""
        statement = (
            sa.select(r.c.id, r.c.options)
            .where(r.c.project_id == project_id, r.c.applied_at.is_not(None))
            .order_by(r.c.applied_at.desc(), r.c.version.desc().nulls_last())
            .limit(1)
        )
        row = (await self._conn.execute(statement)).first()
        if row is None:
            return None
        options = row.options or {}
        # A change approved on step 7 records the inputs it put in force; otherwise the
        # plan stands on the inputs it was solved from.
        inputs_hash = options.get("appliedInputsHash") or options.get("inputsHash")
        return AppliedRun(row.id, str(inputs_hash) if inputs_hash else None)

    async def change_request_status(
        self, project_id: uuid.UUID, change_request_id: uuid.UUID
    ) -> str | None:
        """Status of one of the project's change requests (None: not found)."""
        changes = change_requests
        return (
            await self._conn.execute(
                sa.select(changes.c.status).where(
                    changes.c.project_id == project_id, changes.c.id == change_request_id
                )
            )
        ).scalar()

    async def mark_replanning(
        self, project_id: uuid.UUID, change_request_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        """Point the change request at the run re-planning it (step 7)."""
        changes = change_requests
        await self._conn.execute(
            sa.update(changes)
            .where(changes.c.project_id == project_id, changes.c.id == change_request_id)
            .values(proposed_run_id=run_id, status="replanning", error=None)
        )


# --------------------------------------------------------------------------- worker side
class OptimizationJobStore:
    """Writes made by the background worker on a service connection (bypasses RLS).

    `planned_assignments` and `recommendations` have no user write policies:
    only this trusted job writes them.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def claim(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        """Move a queued run to running; returns its input snapshot (None if not queued)."""
        stmt = (
            sa.update(r)
            .where(r.c.id == run_id, r.c.status == SolverStatus.QUEUED)
            .values(status=SolverStatus.RUNNING, started_at=sa.func.now())
            .returning(r.c.input_snapshot)
        )
        row = (await self._conn.execute(stmt)).first()
        return dict(row.input_snapshot) if row else None

    async def finish(self, run_id: uuid.UUID, report: Report) -> None:
        await self._conn.execute(
            sa.update(r)
            .where(r.c.id == run_id)
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
                            "wbs_node_id": uuid.UUID(a.wbs_node_id),
                            "employee_id": uuid.UUID(a.employee_id) if a.employee_id else None,
                            "contractor_label": a.contractor_label,
                            "start_week": a.start_week,
                            "end_week": a.end_week,
                            "hours_per_week": a.hours_per_week,
                            "baseline_start_week": a.baseline_start_week,
                            "baseline_end_week": a.baseline_end_week,
                            "baseline_employee_id": (
                                uuid.UUID(a.baseline_employee_id)
                                if a.baseline_employee_id
                                else None
                            ),
                        }
                        for a in report.assignments
                    ]
                )
            )
        if report.recommendations:
            await self._conn.execute(
                sa.insert(recommendations).values(
                    [
                        {
                            "run_id": run_id,
                            "type": rec.type,
                            "wbs_node_id": uuid.UUID(rec.wbs_node_id) if rec.wbs_node_id else None,
                            "before_value": rec.before_value,
                            "after_value": rec.after_value,
                            "reason": rec.reason,
                            "sort_order": rec.sort_order,
                        }
                        for rec in report.recommendations
                    ]
                )
            )

    async def fail(self, run_id: uuid.UUID, error: str) -> None:
        await self._conn.execute(
            sa.update(r)
            .where(r.c.id == run_id, r.c.status.in_(ACTIVE))
            .values(status=SolverStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        result = await self._conn.execute(
            sa.update(r)
            .where(r.c.status.in_(ACTIVE), _older_than(older_than_s))
            .values(status=SolverStatus.FAILED, error=error, finished_at=sa.func.now())
        )
        return int(result.rowcount)
