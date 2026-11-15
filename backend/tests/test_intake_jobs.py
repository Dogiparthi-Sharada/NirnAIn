"""Tests of the intake background runner's life cycle.

Purpose: Analysis must never block requests, skip runs that aren't queued, mark failures without
leaking internals, survive database outages and recover runs a restart interrupted.
How it fits: Exercises app/jobs/intake.py `IntakeRunner` and the service store of
app/jobs/background.py with the fake job store of tests/support/intake_jobs.py; layer: tests.
Created: 2026-10-21 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.ai.intake_llm import ExtractionOutput
from app.ai.llm import Usage
from app.jobs.background import service_stores
from app.jobs.intake import (
    FAILED_ERROR,
    IntakeRunner,
)
from app.repositories.intake_jobs import IntakeJobStore
from tests.support.intake_jobs import FakeJobStore, extracted_item, fake_extract, runner_settings
from tests.support.runners import drain


async def test_run_not_queued_is_skipped() -> None:
    """A run that is no longer queued is left alone."""
    store = FakeJobStore()
    store.claimed = None
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
    assert (store.finished, store.failed) == ([], [])


async def test_unexpected_crash_fails_without_leaking(monkeypatch: pytest.MonkeyPatch) -> None:
    """A crash fails the run with a fixed message, never the error text."""

    def boom(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        """Crash with secret internals in the message."""
        raise RuntimeError("secret internals")

    monkeypatch.setattr(IntakeRunner, "_extract", boom)
    store = FakeJobStore()
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    await drain(runner)
    assert store.failed == [(run_id, FAILED_ERROR)]
    assert "secret" not in FAILED_ERROR


async def test_fail_swallows_db_errors() -> None:
    """A database outage while claiming and failing is logged, not raised."""
    store = FakeJobStore()
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    store.broken = True
    await runner.submit(uuid.uuid4())
    await drain(runner)  # the claim fails, then marking failed fails: both logged


async def test_recover_stale() -> None:
    """Stale runs are failed on start; outages and a missing database recover none."""
    store = FakeJobStore()
    store.stale = 2
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    assert await runner.recover_stale(180) == 2
    store.broken = True
    assert await runner.recover_stale(180) == 0
    assert await IntakeRunner(None, runner_settings()).recover_stale(180) == 0


async def test_without_database_runs_stay_queued() -> None:
    """Without a database nothing is started."""
    runner = IntakeRunner(None, runner_settings())
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

    async with service_stores(Engine(), IntakeJobStore)() as store:  # type: ignore[arg-type]
        assert store._conn == "conn"  # type: ignore[comparison-overlap]
    assert log == ["begin", "commit"]


async def test_background_task_schedules_on_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run queued as a Starlette background task runs on the event loop."""
    from starlette.background import BackgroundTasks

    result = ExtractionOutput(items=[extracted_item("risk", "X")])
    monkeypatch.setattr(IntakeRunner, "_extract", fake_extract(result))
    store = FakeJobStore()
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    tasks = BackgroundTasks()
    tasks.add_task(runner.submit, uuid.uuid4())
    await tasks()  # what Starlette does after sending the response
    await drain(runner)
    assert len(store.finished) == 1
