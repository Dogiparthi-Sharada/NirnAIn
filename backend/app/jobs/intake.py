"""In-process AI intake worker (plan.md §0: no Redis yet).

Purpose: Analyze a run's notes (pasted text plus readable uploads) with Claude on a worker thread,
or with the labelled rule-based extractor when there is no key or the call fails (the run's model is
then 'rules'), and write the scope items on a service connection.
How it fits: The API (app/api/v1/intake.py) commits a queued intake run and calls
`IntakeRunner.submit`; writes go through app/repositories/intake_jobs.py; lifecycle from
app/jobs/background.py; layer: jobs.
Created: 2026-10-21 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager

import anthropic

from app.ai.evidence import ScopeSuggestion
from app.ai.intake import Source, fit_sources, notes_block, with_spoken_text
from app.ai.intake_llm import (
    SYSTEM_PROMPT,
    ExtractionOutput,
    extraction_prompt,
    suggestions_from_model,
)
from app.ai.intake_rules import RULES_MODEL, extract_by_rules
from app.ai.llm import LLM, Usage, make_client
from app.core.config import Settings
from app.jobs.background import BackgroundRunner, RunnerMessages
from app.repositories.intake_jobs import IntakeJobStore

logger = logging.getLogger("app.jobs.intake")

RESTART_ERROR = "The server restarted during the run. Please try again."
FAILED_ERROR = "AI intake hit an unexpected error. Please try again."
EMPTY_ERROR = "No project notes were found to analyze."

StoreFactory = Callable[[], AbstractAsyncContextManager[IntakeJobStore]]


class IntakeRunner(BackgroundRunner[IntakeJobStore]):
    """Runs queued intake runs as background tasks on the API's event loop."""

    logger = logger
    messages = RunnerMessages(
        unavailable="intake worker unavailable; run left queued",
        mark_failed="could not mark intake run failed",
        recovery_failed="intake startup recovery failed",
        recovered="marked orphaned intake runs failed",
    )
    task_prefix = "intake"
    id_key = "run_id"
    restart_error = RESTART_ERROR

    def __init__(self, store: StoreFactory | None, settings: Settings) -> None:
        """Keep the store factory and the Claude client intake uses (`INTAKE_MODEL`)."""
        super().__init__(store)
        self._llm = LLM(make_client(settings), settings.intake_model)

    async def _run(self, run_id: uuid.UUID) -> None:
        """Claim a queued run, read its notes, store the suggestions; failures end the run."""
        assert self._store is not None  # noqa: S101 - guarded by submit()
        log_extra = {"run_id": str(run_id)}
        try:
            async with self._store() as store:
                claimed = await store.claim(run_id)
            if claimed is None:
                logger.info("intake run not queued; skipping", extra=log_extra)
                return
            sources = fit_sources(await asyncio.to_thread(with_spoken_text, claimed.sources))
            if not sources:
                await self.fail(run_id, EMPTY_ERROR)
                return
            logger.info(
                "intake started",
                extra={
                    **log_extra,
                    "sources": len(sources),
                    "truncated": sum(source.truncated for source in sources),
                },
            )
            model, suggestions, usage = await self._suggest(sources)
            async with self._store() as store:
                await store.finish(
                    run_id,
                    claimed.project_id,
                    model,
                    suggestions,
                    usage.input_tokens,
                    usage.output_tokens,
                )
            logger.info(
                "intake finished", extra={**log_extra, "items": len(suggestions), "model": model}
            )
        except asyncio.CancelledError:
            await self.fail(run_id, RESTART_ERROR)
            raise
        except Exception:
            logger.exception("intake run failed", extra=log_extra)
            await self.fail(run_id, FAILED_ERROR)

    async def _suggest(self, sources: Sequence[Source]) -> tuple[str, list[ScopeSuggestion], Usage]:
        """Scope suggestions from Claude; from the rules when no key is set or Claude fails."""
        if not self._llm.configured:
            return await self._suggest_by_rules(sources)
        try:
            output, usage = await asyncio.to_thread(self._extract, notes_block(sources))
            suggestions = await asyncio.to_thread(suggestions_from_model, output.items, sources)
        except (anthropic.APIError, ValueError):
            # An API error, a refusal or an answer that fails validation
            # (pydantic's ValidationError is a ValueError) must not lose the
            # run: the labelled rules extractor reads the same notes instead.
            logger.warning("intake Claude call failed; using the rules extractor", exc_info=True)
            return await self._suggest_by_rules(sources)
        return self._llm.model, suggestions, usage

    @staticmethod
    async def _suggest_by_rules(
        sources: Sequence[Source],
    ) -> tuple[str, list[ScopeSuggestion], Usage]:
        """Labelled fallback: the run records model 'rules' and uses no tokens."""
        suggestions = await asyncio.to_thread(extract_by_rules, sources)
        return RULES_MODEL, suggestions, Usage(0, 0)

    def _extract(self, notes: str) -> tuple[ExtractionOutput, Usage]:
        """One structured Claude call over the `<project_notes>` block (blocking)."""
        return self._llm.parse(
            system=SYSTEM_PROMPT, prompt=extraction_prompt(notes), output=ExtractionOutput
        )
