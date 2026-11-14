"""In-process worker for AI jobs: WBS generation and validation.

Same pattern as `app.jobs.intake`: the API commits a queued `ai_jobs` row and
calls `AiJobRunner.submit`. Claude runs on a worker thread (the SDK's sync
client blocks) and everything is read and written on a service connection —
a trusted system job, scoped to the job's project explicitly.

Without ANTHROPIC_API_KEY, generation uses a deterministic template (model
'template': one deliverable per accepted deliverable scope item, each with
design / build / test work packages) and validation runs only the built-in
checks (model 'rules').
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai.llm import LLM, Usage, make_client
from app.ai.wbs_generate import (
    GENERATE_SYSTEM,
    NO_SCOPE,
    GeneratedWbs,
    NothingToPlanError,
    build_generation_prompt,
    plan_from_model,
    select_scope,
    suggest_owners,
    template_plan,
)
from app.ai.wbs_review import (
    REVIEW_UNAVAILABLE,
    VALIDATE_SYSTEM,
    WbsReview,
    build_validation_prompt,
    review_issues,
)
from app.ai.wbs_validate import Issue, validation_result
from app.core.config import Settings
from app.domain.common import AiJobKind
from app.repositories.ai_jobs import AiJobStore, ClaimedJob

logger = logging.getLogger("app.jobs.ai")

RESTART_ERROR = "The server restarted during the job. Please try again."
FAILED_ERROR = "The AI job hit an unexpected error. Please try again."
GONE_ERROR = "The project no longer exists."
HAS_WBS_ERROR = "This project already has a WBS. Delete it to generate a new one."
EMPTY_PLAN_ERROR = "The AI returned no work packages. Please try again."
TEMPLATE_MODEL = "template"
RULES_MODEL = "rules"

StoreFactory = Callable[[], AbstractAsyncContextManager[AiJobStore]]


class JobFailedError(Exception):
    """Ends a job with a user-facing message."""


def engine_store(engine: AsyncEngine) -> StoreFactory:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AiJobStore]:
        async with engine.begin() as conn:
            yield AiJobStore(conn)

    return factory


class AiJobRunner:
    def __init__(
        self, store: StoreFactory | None, settings: Settings, llm: LLM | None = None
    ) -> None:
        self._store = store
        self._llm = llm or LLM(make_client(settings), settings.ai_model)
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    @property
    def active(self) -> int:
        return len(self._tasks)

    @property
    def ai_configured(self) -> bool:
        return self._llm.configured

    async def submit(self, job_id: uuid.UUID) -> None:
        """Start a committed, queued job in the background (async: see IntakeRunner.submit)."""
        if self._store is None or self._closing:
            logger.warning("AI worker unavailable; job left queued", extra={"job_id": str(job_id)})
            return
        task = asyncio.get_running_loop().create_task(self._run(job_id), name=f"ai-{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: uuid.UUID) -> None:
        assert self._store is not None  # noqa: S101 - guarded by submit()
        extra = {"job_id": str(job_id)}
        try:
            async with self._store() as store:
                claimed = await store.claim(job_id)
            if claimed is None:
                logger.info("AI job not queued; skipping", extra=extra)
                return
            logger.info("AI job started", extra={**extra, "kind": claimed.kind})
            if claimed.kind == AiJobKind.WBS_GENERATE:
                await self._generate(job_id, claimed)
            else:
                await self._validate(job_id, claimed)
            logger.info("AI job finished", extra=extra)
        except asyncio.CancelledError:
            await self.fail(job_id, RESTART_ERROR)
            raise
        except JobFailedError as exc:
            await self.fail(job_id, str(exc))
        except Exception:
            logger.exception("AI job failed", extra=extra)
            await self.fail(job_id, FAILED_ERROR)

    # ----------------------------------------------------------------- generate
    async def _generate(self, job_id: uuid.UUID, job: ClaimedJob) -> None:
        assert self._store is not None  # noqa: S101
        async with self._store() as store:
            inp = await store.generation_input(job.project_id)
        if inp is None:
            raise JobFailedError(GONE_ERROR)

        usage: Usage | None = None
        try:
            if self._llm.configured:
                if not select_scope(inp.scope):
                    raise NothingToPlanError(NO_SCOPE)
                prompt, refs = build_generation_prompt(inp)
                generated, usage = await asyncio.to_thread(
                    self._llm.parse, system=GENERATE_SYSTEM, prompt=prompt, output=GeneratedWbs
                )
                plan = plan_from_model(generated, refs)
                if plan.empty:
                    raise JobFailedError(EMPTY_PLAN_ERROR)
                model = self._llm.model
            else:
                plan = template_plan(inp)
                model = TEMPLATE_MODEL
        except NothingToPlanError as exc:
            raise JobFailedError(str(exc)) from exc

        owners = suggest_owners(plan.packages, inp.team)
        async with self._store() as store:
            counts = await store.write_plan(job.project_id, plan, owners)
            if counts is None:
                raise JobFailedError(HAS_WBS_ERROR)
            result: dict[str, Any] = {**counts, "ownersSuggested": len(owners)}
            await store.finish(
                job_id,
                result,
                model,
                usage.input_tokens if usage else None,
                usage.output_tokens if usage else None,
            )

    # ----------------------------------------------------------------- validate
    async def _validate(self, job_id: uuid.UUID, job: ClaimedJob) -> None:
        assert self._store is not None  # noqa: S101
        async with self._store() as store:
            inp = await store.validation_input(job.project_id)
        if inp is None:
            raise JobFailedError(GONE_ERROR)

        extra: list[Issue] = []
        usage: Usage | None = None
        model = RULES_MODEL
        if self._llm.configured and inp.nodes:
            try:
                review, usage = await asyncio.to_thread(
                    self._llm.parse,
                    system=VALIDATE_SYSTEM,
                    prompt=build_validation_prompt(inp),
                    output=WbsReview,
                    max_tokens=4000,
                )
                extra = review_issues(review)
                model = self._llm.model
            except Exception:
                # The built-in checks still stand; say honestly that the review didn't run.
                logger.exception("WBS review call failed", extra={"job_id": str(job_id)})
                extra = [Issue("warning", REVIEW_UNAVAILABLE)]
        result = validation_result(inp, extra)
        async with self._store() as store:
            await store.finish(
                job_id,
                result,
                model,
                usage.input_tokens if usage else None,
                usage.output_tokens if usage else None,
            )

    # ----------------------------------------------------------------- lifecycle
    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        """Mark a queued/running job failed (also used for jobs orphaned by a restart)."""
        if self._store is None:
            return
        try:
            async with self._store() as store:
                await store.fail(job_id, error)
        except Exception:
            logger.exception("could not mark AI job failed", extra={"job_id": str(job_id)})

    async def recover_stale(self, stale_after_s: int) -> int:
        """Fail jobs orphaned by a previous process. Never raises."""
        if self._store is None:
            return 0
        try:
            async with self._store() as store:
                count = await store.fail_stale(stale_after_s, RESTART_ERROR)
        except Exception:
            logger.exception("AI job startup recovery failed")
            return 0
        if count:
            logger.warning("marked orphaned AI jobs failed", extra={"count": count})
        return count

    async def shutdown(self, timeout_s: float = 10.0) -> None:
        self._closing = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)
