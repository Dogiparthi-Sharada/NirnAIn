"""In-process worker for AI jobs: WBS generation and validation.

Purpose: Run long AI work after the request: Claude runs on a worker thread (the SDK blocks) and
results are written on a service connection scoped to the job's project. Without ANTHROPIC_API_KEY,
generation uses the deterministic template (model 'template') and validation runs only the built-in
checks (model 'rules'). Each successful Claude call also records its tokens in `ai_usage_events`
(the job keeps its own token columns too).
How it fits: The API (app/api/v1/ai.py) commits a queued `ai_jobs` row and calls
`AiJobRunner.submit`; reads and writes go through app/repositories/ai_jobs.py; lifecycle from
app/jobs/background.py; layer: jobs.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

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
from app.jobs.background import BackgroundRunner, RunnerMessages
from app.repositories.ai_jobs import AiJobStore, ClaimedJob
from app.repositories.ai_usage import AiFeature, AiUsageEvent, ServiceConnections, record_ai_usage

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


class AiJobRunner(BackgroundRunner[AiJobStore]):
    """Runs queued WBS generate / validate jobs as background tasks on the API's event loop."""

    logger = logger
    messages = RunnerMessages(
        unavailable="AI worker unavailable; job left queued",
        mark_failed="could not mark AI job failed",
        recovery_failed="AI job startup recovery failed",
        recovered="marked orphaned AI jobs failed",
    )
    task_prefix = "ai"
    id_key = "job_id"
    restart_error = RESTART_ERROR

    def __init__(
        self,
        store: StoreFactory | None,
        settings: Settings,
        llm: LLM | None = None,
        usage_log: ServiceConnections | None = None,
    ) -> None:
        """Keep the store factory, the Claude client (`AI_MODEL` unless one is given) and where
        token use is recorded (None: not recorded)."""
        super().__init__(store)
        self._llm = llm or LLM(make_client(settings), settings.ai_model)
        self._usage_log = usage_log

    async def _record_usage(self, feature: AiFeature, job: ClaimedJob, usage: Usage | None) -> None:
        """Record a successful Claude call of the job for its project (never raises)."""
        if usage is None:
            return
        event = AiUsageEvent(
            feature, self._llm.model, usage, project_id=job.project_id, user_id=job.requested_by
        )
        await record_ai_usage(self._usage_log, event)

    @property
    def ai_configured(self) -> bool:
        """Tell whether Claude is configured (else the template and rules run)."""
        return self._llm.configured

    async def _run(self, job_id: uuid.UUID) -> None:
        """Claim a queued job and run it; failures end the job with a user-facing error."""
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
        """Generate the WBS (Claude, else the template), suggest owners and write it.

        Fails the job when the project is gone, has nothing to plan, Claude returns no packages or a
        WBS already exists.
        """
        assert self._store is not None  # noqa: S101
        async with self._store() as store:
            generation_input = await store.generation_input(job.project_id)
        if generation_input is None:
            raise JobFailedError(GONE_ERROR)

        usage: Usage | None = None
        try:
            if self._llm.configured:
                if not select_scope(generation_input.scope):
                    raise NothingToPlanError(NO_SCOPE)
                prompt, refs = build_generation_prompt(generation_input)
                generated, usage = await asyncio.to_thread(
                    self._llm.parse, system=GENERATE_SYSTEM, prompt=prompt, output=GeneratedWbs
                )
                await self._record_usage("wbs_generate", job, usage)
                plan = plan_from_model(generated, refs)
                if plan.empty:
                    raise JobFailedError(EMPTY_PLAN_ERROR)
                model = self._llm.model
            else:
                plan = template_plan(generation_input)
                model = TEMPLATE_MODEL
        except NothingToPlanError as exc:
            raise JobFailedError(str(exc)) from exc

        owners = suggest_owners(plan.packages, generation_input.team)
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
        """Run the built-in checks plus, when configured, the Claude review, and store the result.

        A failed review call is reported as a warning; the built-in checks still stand.
        """
        assert self._store is not None  # noqa: S101
        async with self._store() as store:
            checks_input = await store.validation_input(job.project_id)
        if checks_input is None:
            raise JobFailedError(GONE_ERROR)

        extra: list[Issue] = []
        usage: Usage | None = None
        model = RULES_MODEL
        if self._llm.configured and checks_input.nodes:
            try:
                review, usage = await asyncio.to_thread(
                    self._llm.parse,
                    system=VALIDATE_SYSTEM,
                    prompt=build_validation_prompt(checks_input),
                    output=WbsReview,
                    max_tokens=4000,
                )
                await self._record_usage("wbs_validate", job, usage)
                extra = review_issues(review)
                model = self._llm.model
            except Exception:
                # The built-in checks still stand; say honestly that the review didn't run.
                logger.exception("WBS review call failed", extra={"job_id": str(job_id)})
                extra = [Issue("warning", REVIEW_UNAVAILABLE)]
        result = validation_result(checks_input, extra)
        async with self._store() as store:
            await store.finish(
                job_id,
                result,
                model,
                usage.input_tokens if usage else None,
                usage.output_tokens if usage else None,
            )
