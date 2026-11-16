"""In-process optimization worker (plan.md §0: no Redis yet).

Purpose: Solve a queued run on a worker thread (CP-SAT is CPU-bound) and store its outcome;
infeasible or late plans also get priced remedies. The host restarts the process on every deploy, so
runs stuck longer than `OPTIMIZER_STALE_AFTER_S` are failed on startup and before a 409.
How it fits: The API (app/api/v1/optimization.py) commits a queued run and calls
`OptimizationRunner.submit`; writes go through app/repositories/optimization_store.py on a service
connection (bypasses RLS); lifecycle from app/jobs/background.py; layer: jobs.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any

from app.core.config import Settings
from app.jobs.background import BackgroundRunner, RunnerMessages
from app.optimization.explain import Report, build_report
from app.optimization.model import PlanningInput, SnapshotError
from app.optimization.outcome import SolveOutcome
from app.optimization.remedies import price_remedies
from app.optimization.runtime import solver_settings
from app.optimization.solver import SolveControl, SolveOptions, solve
from app.repositories.optimization_store import OptimizationJobStore

logger = logging.getLogger("app.jobs.optimizer")

RESTART_ERROR = "The server restarted during the run. Please run again."
FAILED_ERROR = "The optimizer hit an unexpected error. Please run again."
SNAPSHOT_ERROR = "The project's planning data couldn't be read. Please run again."

StoreFactory = Callable[[], AbstractAsyncContextManager[OptimizationJobStore]]


def solve_options(settings: Settings) -> SolveOptions:
    """How every run is solved (`OPTIMIZER_TIME_LIMIT_S`, `OPTIMIZER_WORKERS`)."""
    return SolveOptions(
        time_limit_s=settings.optimizer_time_limit_s, workers=settings.optimizer_workers
    )


def run_settings(settings: Settings, planning_input: PlanningInput) -> dict[str, Any]:
    """`options.solverSettings` of a run of `planning_input` (round 3, R7)."""
    return solver_settings(solve_options(settings), planning_input.constraints)


def _slips(report: Report) -> bool:
    """Whether the plan finishes after its target date (`summary.slipDays`)."""
    summary = (report.result or {}).get("summary") or {}
    return bool(summary.get("slipDays"))


class OptimizationRunner(BackgroundRunner[OptimizationJobStore]):
    """Solves queued optimization runs in the background, then prices remedies when needed."""

    logger = logger
    messages = RunnerMessages(
        unavailable="optimizer unavailable; run left queued",
        mark_failed="could not mark optimization run failed",
        recovery_failed="optimizer startup recovery failed",
        recovered="marked orphaned optimization runs failed",
    )
    task_prefix = "opt"
    id_key = "run_id"
    restart_error = RESTART_ERROR

    def __init__(self, store: StoreFactory | None, settings: Settings) -> None:
        """Keep the store factory, the solve options and the stale-run timeout."""
        super().__init__(store)
        self._options = solve_options(settings)
        self._stale_after_s = settings.optimizer_stale_after_s
        self._controls: dict[uuid.UUID, SolveControl] = {}

    async def _run(self, run_id: uuid.UUID) -> None:
        """Claim a queued run, solve its frozen input, add remedies if needed and store the report.

        Failures end the run with a user-facing error; a shutdown records the restart error.
        """
        assert self._store is not None  # noqa: S101 - guarded by submit()
        extra = {"run_id": str(run_id)}
        control = SolveControl()
        self._controls[run_id] = control
        try:
            async with self._store() as store:
                snapshot = await store.claim(run_id)
            if snapshot is None:
                logger.info("optimization run not queued; skipping", extra=extra)
                return
            planning_input = PlanningInput.from_snapshot(snapshot)
            logger.info(
                "optimization started",
                extra={
                    **extra,
                    "tasks": len(planning_input.tasks),
                    "employees": len(planning_input.employees),
                },
            )
            outcome = await asyncio.to_thread(solve, planning_input, self._options, control)
            report = build_report(planning_input, outcome)
            if outcome.status == "infeasible" or _slips(report):
                report = await self._with_remedies(planning_input, outcome, report, control, extra)
            async with self._store() as store:
                await store.finish(run_id, report)
            logger.info(
                "optimization finished",
                extra={**extra, "status": report.status, "runtime_ms": report.runtime_ms},
            )
        except asyncio.CancelledError:
            await self.fail(run_id, RESTART_ERROR)
            raise
        except SnapshotError:
            logger.exception("optimization snapshot invalid", extra=extra)
            await self.fail(run_id, SNAPSHOT_ERROR)
        except Exception:
            logger.exception("optimization run failed", extra=extra)
            await self.fail(run_id, FAILED_ERROR)
        finally:
            self._controls.pop(run_id, None)

    async def _with_remedies(
        self,
        planning_input: PlanningInput,
        outcome: SolveOutcome,
        report: Report,
        control: SolveControl,
        extra: dict[str, str],
    ) -> Report:
        """Add the priced remedies to an infeasible or late report (`result.remedies`), and the
        options tried but not offered with why (`result.triedRemedies`).

        Best effort: if pricing fails the run still reports its plan or conflicts.
        """
        current = outcome if outcome.plan is not None else None
        try:
            priced = await asyncio.to_thread(
                price_remedies, planning_input, self._options, control, current, outcome.conflicts
            )
        except Exception:
            logger.exception("pricing remedies failed", extra=extra)
            return report
        result = {
            **(report.result or {}),
            "remedies": [remedy.to_json() for remedy in priced.offered],
            "triedRemedies": [entry.to_json() for entry in priced.tried],
        }
        return replace(report, result=result)

    async def recover_stale(self, stale_after_s: int | None = None) -> int:
        """Fail runs orphaned by a previous process (after `OPTIMIZER_STALE_AFTER_S`)."""
        return await super().recover_stale(
            self._stale_after_s if stale_after_s is None else stale_after_s
        )

    def _stop_blocking_work(self) -> None:
        """Stop the CP-SAT solves running on worker threads."""
        for control in list(self._controls.values()):
            control.stop()
