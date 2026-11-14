"""Optimization runs as the user sees them: queueing, reading and linking re-plans (RLS session).

Purpose: Enforce one run at a time per project (NFR-005), freeze a run's input at queue time and
show runs, the plan in force and its inputs hash, so the screen can tell when a plan is outdated.
How it fits: Used by app/api/v1/optimization.py; planning data is read by
app/repositories/planning_input.py and app/repositories/input_facts.py; the worker's writes live in
app/repositories/optimization_store.py; layer: repositories.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
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
from app.optimization.inputs import InputFacts
from app.optimization.model import PlanningInput
from app.repositories import access
from app.repositories.input_facts import InputFactsReader
from app.repositories.job_rows import started_before
from app.repositories.planning_input import PlanningInputReader, ProjectContext
from app.repositories.tables import (
    change_requests,
    optimization_runs,
    planned_assignments,
)
from app.repositories.work_packages import packages_without_duration

__all__ = [
    "ActiveRun",
    "ActiveRunExistsError",
    "AppliedRun",
    "OptimizationRepository",
    "ProjectContext",
]

ACTIVE = (SolverStatus.QUEUED, SolverStatus.RUNNING)
runs = optimization_runs


class ActiveRunExistsError(Exception):
    """The one-active-run-per-project unique index rejected the insert."""


@dataclass(frozen=True, slots=True)
class ActiveRun:
    """A queued or running run and whether it has been active for too long (orphaned)."""

    id: uuid.UUID
    stale: bool


@dataclass(frozen=True, slots=True)
class AppliedRun:
    """The plan in force and the inputs hash it was solved from (null for older runs)."""

    id: uuid.UUID
    inputs_hash: str | None


def _run_select() -> sa.Select[Any]:
    """Return the select of the run columns the API shows."""
    return sa.select(
        runs.c.id,
        runs.c.project_id,
        runs.c.status,
        runs.c.solver,
        runs.c.runtime_ms,
        runs.c.error,
        runs.c.infeasibility,
        runs.c.result,
        runs.c.created_at,
        runs.c.started_at,
        runs.c.finished_at,
        runs.c.version,
        runs.c.applied_at,
        runs.c.options,
    )


def _to_run(row: sa.Row[Any]) -> OptimizationRun:
    """Turn a run row into the API model (runtime in seconds, infeasibility as `iis`)."""
    mapping = row._mapping
    return OptimizationRun.model_validate(
        {
            "id": mapping["id"],
            "project_id": mapping["project_id"],
            "status": mapping["status"],
            "solver": mapping["solver"],
            "solve_seconds": None
            if mapping["runtime_ms"] is None
            else mapping["runtime_ms"] / 1000,
            "error": mapping["error"],
            "iis": mapping["infeasibility"],
            "result": mapping["result"],
            "created_at": mapping["created_at"],
            "started_at": mapping["started_at"],
            "finished_at": mapping["finished_at"],
            "version": mapping.get("version"),
            "applied_at": mapping.get("applied_at"),
            "options": mapping.get("options") or {},
        }
    )


class OptimizationRepository:
    """User-facing queries: everything runs under the caller's RLS session."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectContext | None:
        """Return the project when the caller can see it, else None."""
        return await access.visible_project(self._conn, project_id)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        """Tell whether the caller may change the project."""
        return await access.can_write_project(self._conn, project_id)

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
        """Return the project's active run, flagged stale after `stale_after_s`, or None."""
        stmt = (
            sa.select(runs.c.id, started_before(runs, stale_after_s).label("stale"))
            .where(runs.c.project_id == project_id, runs.c.status.in_(ACTIVE))
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return ActiveRun(id=row.id, stale=bool(row.stale)) if row else None

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        """Mark an active run failed with the error shown to the user."""
        await self._conn.execute(
            sa.update(runs)
            .where(runs.c.id == run_id, runs.c.status.in_(ACTIVE))
            .values(status=SolverStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def create(
        self,
        project_id: uuid.UUID,
        snapshot: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """Queue a run with its frozen input snapshot and options; returns the new id.

        Raises `ActiveRunExistsError` when the one-active-run unique index rejects it.
        """
        # Id generated here and the row re-selected, as for projects (RETURNING
        # visibility depends on SELECT policies).
        run_id = uuid.uuid4()
        try:
            await self._conn.execute(
                sa.insert(runs).values(
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
        """Return one of the project's runs, or None."""
        stmt = _run_select().where(runs.c.project_id == project_id, runs.c.id == run_id)
        row = (await self._conn.execute(stmt)).first()
        return _to_run(row) if row else None

    async def latest(self, project_id: uuid.UUID) -> OptimizationRun | None:
        """Return the project's newest run, or None."""
        stmt = (
            _run_select()
            .where(runs.c.project_id == project_id)
            .order_by(runs.c.created_at.desc(), runs.c.id.desc())
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return _to_run(row) if row else None

    # ------------------------------------------------------------------ input
    async def load_input(self, project: ProjectContext, today: date) -> PlanningInput:
        """The project's planning data, frozen into the optimizer's input (see planning_input)."""
        return await PlanningInputReader(self._conn).load(project, today)

    async def input_facts(
        self, project: ProjectContext, planning_input: PlanningInput
    ) -> InputFacts:
        """Return what the confirmation sheet shows beyond the input (answers and sources)."""
        return await InputFactsReader(self._conn).facts(project, planning_input)

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
            sa.select(runs.c.id, runs.c.options)
            .where(runs.c.project_id == project_id, runs.c.applied_at.is_not(None))
            .order_by(runs.c.applied_at.desc(), runs.c.version.desc().nulls_last())
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
