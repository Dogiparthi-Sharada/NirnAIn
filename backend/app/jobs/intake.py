"""In-process AI intake worker (plan.md §0: no Redis yet).

Mirrors `app.jobs.optimizer`: the API commits a queued intake run and calls
`IntakeRunner.submit`. The run's notes (pasted text + readable uploads) are
analyzed by Claude on a worker thread (`asyncio.to_thread` — the SDK's sync
client blocks), or by the deterministic rule-based extractor when there is
no API key or the Claude call fails (the run's model is then 'rules'). The
outcome is written on a service connection, which bypasses RLS: this is a
trusted system job, same as how optimization results are written.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import anthropic
from sqlalchemy.ext.asyncio import AsyncEngine

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
from app.repositories.intake_jobs import IntakeJobStore

logger = logging.getLogger("app.jobs.intake")

RESTART_ERROR = "The server restarted during the run. Please try again."
FAILED_ERROR = "AI intake hit an unexpected error. Please try again."
EMPTY_ERROR = "No project notes were found to analyze."

StoreFactory = Callable[[], AbstractAsyncContextManager[IntakeJobStore]]


def engine_store(engine: AsyncEngine) -> StoreFactory:
    """Each use opens one service-connection transaction (committed on success)."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[IntakeJobStore]:
        async with engine.begin() as conn:
            yield IntakeJobStore(conn)

    return factory


class IntakeRunner:
    """Runs queued intake runs as background tasks on the API's event loop."""

    def __init__(self, store: StoreFactory | None, settings: Settings) -> None:
        self._store = store
        self._llm = LLM(make_client(settings), settings.intake_model)
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    @property
    def active(self) -> int:
        """Number of runs in progress in this process."""
        return len(self._tasks)

    async def submit(self, run_id: uuid.UUID) -> None:
        """Start analyzing a committed, queued run in the background.

        Async on purpose: Starlette runs sync background tasks in a thread
        pool, where there is no event loop to schedule the job on.
        """
        if self._store is None or self._closing:
            logger.warning(
                "intake worker unavailable; run left queued", extra={"run_id": str(run_id)}
            )
            return
        task = asyncio.get_running_loop().create_task(self._run(run_id), name=f"intake-{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, run_id: uuid.UUID) -> None:
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
                await self._fail(run_id, EMPTY_ERROR)
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
            await self._fail(run_id, RESTART_ERROR)
            raise
        except Exception:
            logger.exception("intake run failed", extra=log_extra)
            await self._fail(run_id, FAILED_ERROR)

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

    async def _fail(self, run_id: uuid.UUID, error: str) -> None:
        assert self._store is not None  # noqa: S101
        try:
            async with self._store() as store:
                await store.fail(run_id, error)
        except Exception:
            logger.exception("could not mark intake run failed", extra={"run_id": str(run_id)})

    async def recover_stale(self, stale_after_s: int) -> int:
        """Fail runs orphaned by a previous process. Never raises."""
        if self._store is None:
            return 0
        try:
            async with self._store() as store:
                count = await store.fail_stale(stale_after_s, RESTART_ERROR)
        except Exception:
            logger.exception("intake startup recovery failed")
            return 0
        if count:
            logger.warning("marked orphaned intake runs failed", extra={"count": count})
        return count

    async def shutdown(self, timeout_s: float = 10.0) -> None:
        """Cancel in-flight runs (each is marked failed with `RESTART_ERROR`)."""
        self._closing = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)
