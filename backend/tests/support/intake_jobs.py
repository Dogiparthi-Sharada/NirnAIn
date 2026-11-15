"""Fake intake job store and helpers for the intake runner tests.

Purpose: The runner tests drive a real `IntakeRunner` without a database or Claude: one claimed run,
recorded finishes and failures, and Claude's answer chosen by the test.
How it fits: Used by tests/test_intake_jobs.py and test_intake_extraction.py in place of
app/repositories/intake_jobs.py and `IntakeRunner._extract`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.ai.evidence import ScopeSuggestion
from app.ai.intake import Source
from app.ai.intake_llm import ExtractedItem, ExtractionOutput
from app.ai.llm import Usage
from app.core.config import Settings
from app.domain.common import Evidence
from app.jobs.intake import (
    RESTART_ERROR,
    IntakeRunner,
)
from app.repositories.intake_jobs import ClaimedRun

Finished = tuple[uuid.UUID, uuid.UUID, str, list[ScopeSuggestion], int, int]


class FakeJobStore:
    """Intake job store that hands out one claimed run and records finishes and failures."""

    def __init__(self, text: str = "Some notes", uploads: tuple[Source, ...] = ()) -> None:
        """Queue one run whose sources are the pasted `text` and the `uploads`."""
        sources = [Source(None, "Pasted notes", text), *uploads]
        self.claimed: ClaimedRun | None = ClaimedRun(uuid.uuid4(), sources)
        self.finished: list[Finished] = []
        self.failed: list[tuple[uuid.UUID, str]] = []
        self.stale = 0
        self.broken = False

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeJobStore]:
        """Open the store as a session, or fail like a database that is down."""
        if self.broken:
            raise RuntimeError("db down")
        yield self

    async def claim(self, run_id: uuid.UUID) -> ClaimedRun | None:
        """Return the queued run once, then None."""
        claimed, self.claimed = self.claimed, None  # claim once
        return claimed

    async def finish(
        self,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        model: str,
        suggestions: list[ScopeSuggestion],
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record the finished run with its suggestions and token counts."""
        self.finished.append((run_id, project_id, model, suggestions, input_tokens, output_tokens))

    async def fail(self, run_id: uuid.UUID, error: str) -> None:
        """Record the failed run with its error."""
        self.failed.append((run_id, error))

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        """Return the number of stale runs to fail with the restart error."""
        assert error == RESTART_ERROR
        return self.stale


def runner_settings(with_key: bool = True) -> Settings:
    """Return settings with or without an Anthropic key."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None, anthropic_api_key="sk-test-key" if with_key else None
    )


def extracted_item(
    category: str, text: str, quote: str | None = None, evidence: Evidence = Evidence.STATED
) -> ExtractedItem:
    """Return an extracted item as Claude would give it."""
    return ExtractedItem(
        category=category,
        text=text,
        confidence=0.9,
        source_quote=quote,
        evidence=evidence,
        evidence_note=None,
        suggested_text=None,
    )


def fake_extract(result: ExtractionOutput, tokens: tuple[int, int] = (100, 50)) -> Any:
    """Return a replacement for `IntakeRunner._extract` that gives `result` and token counts."""

    def extract(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        """Return the fixed extraction and usage."""
        return result, Usage(*tokens)

    return extract
