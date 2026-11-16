"""Turns source text into a `ProjectDraft`: Claude when configured, else the rules.

Purpose: "Read it" always answers: a failed AI call falls back to the rules too, and the draft's
`model` says which reader filled it in ("rules" for the fallback) so the web app can label it. A
Claude read also returns its token use, which the endpoint records.
How it fits: app/api/v1/drafts.py gets one shared reader per app from `get_draft_reader`; it calls
app/drafts/ai.py or app/drafts/rules.py; layer: drafts.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
import logging

import anthropic
from fastapi import Request

from app.ai.llm import LLM, LLMNotConfiguredError, Usage, make_client
from app.core.config import Settings
from app.domain.drafts import RULES_MODEL, ProjectDraft
from app.drafts.ai import ai_draft
from app.drafts.rules import rules_draft

logger = logging.getLogger("app.drafts")


class DraftReader:
    """Reads drafts with `llm` when it is configured (key set), else with the rules."""

    def __init__(self, llm: LLM | None, timeout_s: float) -> None:
        """Keep the Claude client (None or unconfigured: rules only) and the call's timeout."""
        self._llm = llm
        self._timeout_s = timeout_s

    @classmethod
    def from_settings(cls, settings: Settings) -> DraftReader:
        """Claude (`AI_MODEL`) when ANTHROPIC_API_KEY is set; the assistant's request timeout."""
        return cls(LLM(make_client(settings), settings.ai_model), settings.assistant_timeout_s)

    async def read(self, text: str, source_name: str) -> ProjectDraft:
        """The draft of `text`; never raises for AI failures (logged, then the rules answer)."""
        draft, _usage = await self.read_with_usage(text, source_name)
        return draft

    async def read_with_usage(
        self, text: str, source_name: str
    ) -> tuple[ProjectDraft, Usage | None]:
        """The draft of `text` and the tokens Claude used for it (None when the rules read it)."""
        llm = self._llm
        if llm is not None and llm.configured:
            try:
                fields, usage = await asyncio.to_thread(ai_draft, llm, text, self._timeout_s)
            except (anthropic.APIError, LLMNotConfiguredError, ValueError) as exc:
                # Security rule (SEC-006): the error can quote the notes or the model's
                # answer, so only its class is logged.
                logger.warning(
                    "AI draft extraction failed; using the rules reader",
                    extra={"error": type(exc).__name__},
                )
            else:
                draft = ProjectDraft(
                    fields=fields, text=text, source_name=source_name, model=llm.model
                )
                return draft, usage
        draft = ProjectDraft(
            fields=rules_draft(text), text=text, source_name=source_name, model=RULES_MODEL
        )
        return draft, None


def get_draft_reader(request: Request) -> DraftReader:
    """FastAPI dependency (tests override it): one reader per app, built on first use."""
    state = request.app.state
    reader: DraftReader | None = getattr(state, "draft_reader", None)
    if reader is None:
        reader = DraftReader.from_settings(state.settings)
        state.draft_reader = reader
    return reader
