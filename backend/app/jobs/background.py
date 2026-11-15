"""The shared lifecycle of the in-process background job runners.

Purpose: Optimization runs, intake runs and AI jobs are all queued by a request and run after
its transaction commits, on the API's event loop; they share how jobs are started, failed,
recovered after a restart and stopped on shutdown, so that logic lives once here.
How it fits: Base of `OptimizationRunner` (optimizer.py), `IntakeRunner` (intake.py) and
`AiJobRunner` (ai_jobs.py); `service_stores` opens their service-connection stores; layer: jobs.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


class JobStore(Protocol):
    """The job-row writes every runner needs from its store."""

    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        """Mark an active job failed with a user-facing error."""

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        """Fail every job active for longer than `older_than_s`; return how many."""


def service_stores[StoreT](
    engine: AsyncEngine, store_type: Callable[[AsyncConnection], StoreT]
) -> Callable[[], AbstractAsyncContextManager[StoreT]]:
    """Return a store factory: each use opens one service-connection transaction.

    The transaction commits when the `async with` block ends without an error.
    """

    @asynccontextmanager
    async def factory() -> AsyncIterator[StoreT]:
        """Yield a store on a fresh service-connection transaction."""
        async with engine.begin() as conn:
            yield store_type(conn)

    return factory


@dataclass(frozen=True, slots=True)
class RunnerMessages:
    """What a runner logs, worded for its kind of job."""

    unavailable: str
    mark_failed: str
    recovery_failed: str
    recovered: str


class BackgroundRunner[StoreT: JobStore]:
    """Runs queued jobs as background tasks on the API's event loop.

    Subclasses set the class attributes below and implement `_run(job_id)`.
    """

    logger: logging.Logger
    messages: RunnerMessages
    task_prefix: str
    id_key: str
    restart_error: str

    def __init__(self, store: Callable[[], AbstractAsyncContextManager[StoreT]] | None) -> None:
        """Keep the store factory (None: no database, jobs stay queued)."""
        self._store = store
        # Strong references: the event loop only keeps weak ones to tasks.
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    @property
    def active(self) -> int:
        """Number of jobs in progress in this process."""
        return len(self._tasks)

    async def submit(self, job_id: uuid.UUID) -> None:
        """Start a committed, queued job in the background.

        Async on purpose: Starlette runs sync background tasks in a thread pool, where there
        is no event loop to schedule the job on.
        """
        if self._store is None or self._closing:
            self.logger.warning(self.messages.unavailable, extra={self.id_key: str(job_id)})
            return
        task = asyncio.get_running_loop().create_task(
            self._run(job_id), name=f"{self.task_prefix}-{job_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: uuid.UUID) -> None:
        """Do one job; implemented by each runner."""
        raise NotImplementedError

    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        """Mark a queued or running job failed (also jobs orphaned by a restart). Never raises.

        If the write fails, startup recovery marks the job failed once it goes stale.
        """
        if self._store is None:
            return
        try:
            async with self._store() as store:
                await store.fail(job_id, error)
        except Exception:
            self.logger.exception(self.messages.mark_failed, extra={self.id_key: str(job_id)})

    async def recover_stale(self, stale_after_s: int) -> int:
        """Fail jobs orphaned by a previous process and return how many. Never raises."""
        if self._store is None:
            return 0
        try:
            async with self._store() as store:
                count = await store.fail_stale(stale_after_s, self.restart_error)
        except Exception:
            self.logger.exception(self.messages.recovery_failed)
            return 0
        if count:
            self.logger.warning(self.messages.recovered, extra={"count": count})
        return count

    def _stop_blocking_work(self) -> None:
        """Ask work running on threads to stop before the tasks are cancelled (none here)."""

    async def shutdown(self, timeout_s: float = 10.0) -> None:
        """Cancel in-flight jobs (each records the restart error) and wait for them."""
        self._closing = True
        self._stop_blocking_work()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)
