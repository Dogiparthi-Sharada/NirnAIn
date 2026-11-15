"""Tests of the optimizer background runner: claim, solve off the event loop, write the report.

Purpose: Solving must never block requests, price remedies for infeasible or late plans, fail safely
on bad snapshots and crashes, stop on shutdown and recover runs a restart interrupted.
How it fits: Exercises app/jobs/optimizer.py `OptimizationRunner` with a fake job store;
layer: tests.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import pytest

from app.core.config import Settings
from app.domain.optimization import RunResult
from app.jobs import optimizer as jobs
from app.jobs.background import service_stores
from app.jobs.optimizer import (
    FAILED_ERROR,
    RESTART_ERROR,
    SNAPSHOT_ERROR,
    OptimizationRunner,
)
from app.optimization.explain import Report
from app.optimization.solver import SolveControl
from app.repositories.optimization_store import OptimizationJobStore
from tests.support.runners import drain
from tests.support.solver_inputs import make_input, with_constraints


class FakeJobStore:
    """Optimizer job store that hands out one snapshot and records finishes and failures."""

    def __init__(self) -> None:
        """Queue one run with the default input's snapshot."""
        self.snapshot: dict[str, Any] | None = make_input().to_snapshot()
        self.finished: list[tuple[uuid.UUID, Report]] = []
        self.failed: list[tuple[uuid.UUID, str]] = []
        self.stale = 0
        self.broken = False

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeJobStore]:
        """Open the store as a session, or fail like a database that is down."""
        if self.broken:
            raise RuntimeError("db down")
        yield self

    async def claim(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        """Return the queued snapshot once, then None."""
        snapshot, self.snapshot = self.snapshot, None  # claim once
        return snapshot

    async def finish(self, run_id: uuid.UUID, report: Report) -> None:
        """Record the finished run with its report."""
        self.finished.append((run_id, report))

    async def fail(self, run_id: uuid.UUID, error: str) -> None:
        """Record the failed run with its error."""
        self.failed.append((run_id, error))

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        """Return the number of stale runs to fail with the restart error."""
        assert error == RESTART_ERROR
        return self.stale


def runner_settings() -> Settings:
    """Return settings with a 5-second, 2-worker solve."""
    return Settings(_env_file=None, optimizer_time_limit_s=5, optimizer_workers=2)  # type: ignore[call-arg]


async def test_run_solves_and_writes_report() -> None:
    """A queued run is solved in the background and its report written."""
    store = FakeJobStore()
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    assert runner.active == 1
    await drain(runner)
    ((finished_id, report),) = store.finished
    assert finished_id == run_id
    assert report.status == "optimal"
    assert report.result is not None
    assert store.failed == []


async def test_infeasible_run_gets_priced_remedies() -> None:
    """An infeasible run keeps its conflicts and gets a priced remedy."""
    store = FakeJobStore()
    tight = with_constraints(make_input(), must_finish_by=date(2026, 10, 16), deadline_mode="hard")
    store.snapshot = tight.to_snapshot()
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
    ((_, report),) = store.finished
    assert report.status == "infeasible"
    assert report.iis  # the conflicts are still explained
    assert report.result is not None
    (remedy,) = report.result["remedies"]
    assert remedy["id"] == "move_deadline"
    assert remedy["title"] == "Move go-live to Oct 23 (+7 days)"
    assert remedy["completionDate"] == "2026-10-23"
    RunResult.model_validate(report.result)


async def test_late_run_keeps_its_plan_and_gets_ways_to_keep_the_date() -> None:
    """A late plan is kept, with only the remedies that can help."""
    store = FakeJobStore()
    late = with_constraints(make_input(), must_finish_by=date(2026, 10, 16), deadline_mode="soft")
    store.snapshot = late.to_snapshot()
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
    ((_, report),) = store.finished
    assert report.status == "optimal"
    assert report.result is not None
    assert report.result["summary"]["slipDays"] == 5
    # The FS chain can't be shortened by a contractor or overtime: accept the date.
    assert [remedy["id"] for remedy in report.result["remedies"]] == ["move_deadline"]
    assert report.result["remedies"][0]["costCents"] == 0
    assert report.result["schedule"]  # the plan is kept
    RunResult.model_validate(report.result)


async def test_remedy_pricing_failure_keeps_the_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If pricing remedies fails the conflicts are still written."""

    def broken(*args: Any) -> None:
        """Fail like a bug in remedy pricing."""
        raise RuntimeError("pricing bug")

    monkeypatch.setattr(jobs, "price_remedies", broken)
    store = FakeJobStore()
    tight = with_constraints(make_input(), must_finish_by=date(2026, 10, 16), deadline_mode="hard")
    store.snapshot = tight.to_snapshot()
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
    ((_, report),) = store.finished
    assert (report.status, report.result) == ("infeasible", None)
    assert report.iis
    assert store.failed == []


async def test_run_not_queued_is_skipped() -> None:
    """A run that is no longer queued is left alone."""
    store = FakeJobStore()
    store.snapshot = None
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
    assert (store.finished, store.failed) == ([], [])


async def test_bad_snapshot_fails_safely() -> None:
    """A snapshot that can't be read fails the run with a fixed message."""
    store = FakeJobStore()
    store.snapshot = {"version": 0}
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    await drain(runner)
    assert store.failed == [(run_id, SNAPSHOT_ERROR)]


async def test_solver_crash_fails_without_leaking(monkeypatch: pytest.MonkeyPatch) -> None:
    """A solver crash fails the run with a fixed message, never the error text."""

    def boom(*args: Any) -> None:
        """Crash with secret internals in the message."""
        raise RuntimeError("secret internals")

    monkeypatch.setattr(jobs, "solve", boom)
    store = FakeJobStore()
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    await drain(runner)
    assert store.failed == [(run_id, FAILED_ERROR)]
    assert "secret" not in FAILED_ERROR


async def test_shutdown_stops_solver_and_records_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown stops the running solve, marks it for restart and refuses new runs."""
    started = threading.Event()

    def slow(planning_input: Any, options: Any, control: SolveControl) -> None:
        """Solve until told to stop, then fail."""
        started.set()
        while not control.stopped:
            threading.Event().wait(0.01)
        raise RuntimeError("stopped")

    monkeypatch.setattr(jobs, "solve", slow)
    store = FakeJobStore()
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    await asyncio.to_thread(started.wait, 5)
    await runner.shutdown(timeout_s=5)
    assert store.failed == [(run_id, RESTART_ERROR)]
    await runner.submit(uuid.uuid4())  # closing: ignored
    assert runner.active == 0


async def test_fail_swallows_db_errors() -> None:
    """A database outage while claiming and failing is logged, not raised."""
    store = FakeJobStore()
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    store.broken = True
    await runner.submit(uuid.uuid4())
    await drain(runner)  # the claim fails, then marking failed fails: both logged


async def test_recover_stale() -> None:
    """Stale runs are failed on start; outages and a missing database recover none."""
    store = FakeJobStore()
    store.stale = 2
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    assert await runner.recover_stale() == 2
    store.broken = True
    assert await runner.recover_stale() == 0
    assert await OptimizationRunner(None, runner_settings()).recover_stale() == 0


async def test_without_database_runs_stay_queued() -> None:
    """Without a database nothing is started."""
    runner = OptimizationRunner(None, runner_settings())
    await runner.submit(uuid.uuid4())
    assert runner.active == 0
    await runner.shutdown()


async def test_engine_store_uses_a_transaction() -> None:
    """The service store opens one transaction per session."""
    log: list[str] = []

    class Engine:
        """Engine that records its transaction."""

        @asynccontextmanager
        async def begin(self) -> AsyncIterator[str]:
            """Record the transaction's begin and commit around a fake connection."""
            log.append("begin")
            yield "conn"
            log.append("commit")

    async with service_stores(Engine(), OptimizationJobStore)() as store:  # type: ignore[arg-type]
        assert store._conn == "conn"  # type: ignore[comparison-overlap]
    assert log == ["begin", "commit"]


def test_stale_setting_must_exceed_solve_time() -> None:
    """The stale-run timeout must be longer than the solve time limit."""
    with pytest.raises(ValueError, match="OPTIMIZER_STALE_AFTER_S"):
        Settings(_env_file=None, optimizer_time_limit_s=100, optimizer_stale_after_s=150)  # type: ignore[call-arg]


async def test_background_task_schedules_on_the_event_loop() -> None:
    """A run queued as a Starlette background task runs on the event loop."""
    from starlette.background import BackgroundTasks

    store = FakeJobStore()
    runner = OptimizationRunner(store, runner_settings())  # type: ignore[arg-type]
    tasks = BackgroundTasks()
    tasks.add_task(runner.submit, uuid.uuid4())
    await tasks()  # what Starlette does after sending the response
    await drain(runner)
    assert len(store.finished) == 1
