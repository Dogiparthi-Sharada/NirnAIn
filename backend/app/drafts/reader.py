"""Turns source text into a `ProjectDraft`: Claude when configured, else the rules.

A failed AI call falls back to the rules too, so "Read it" always answers; the
draft's `model` says which reader filled it in ("rules" for the fallback).
"""

from __future__ import annotations

import asyncio
import logging

import anthropic
from fastapi import Request

from app.ai.llm import LLM, LLMNotConfiguredError, make_client
from app.core.config import Settings
from app.domain.drafts import RULES_MODEL, ProjectDraft
from app.drafts.ai import ai_draft
from app.drafts.rules import rules_draft

logger = logging.getLogger("app.drafts")


class DraftReader:
    def __init__(self, llm: LLM | None, timeout_s: float) -> None:
        self._llm = llm
        self._timeout_s = timeout_s

    @classmethod
    def from_settings(cls, settings: Settings) -> DraftReader:
        return cls(LLM(make_client(settings), settings.ai_model), settings.assistant_timeout_s)

    async def read(self, text: str, source_name: str) -> ProjectDraft:
        llm = self._llm
        if llm is not None and llm.configured:
            try:
                fields = await asyncio.to_thread(ai_draft, llm, text, self._timeout_s)
            except (anthropic.APIError, LLMNotConfiguredError, ValueError):
                logger.exception("AI draft extraction failed; using the rules reader")
            else:
                return ProjectDraft(
                    fields=fields, text=text, source_name=source_name, model=llm.model
                )
        return ProjectDraft(
            fields=rules_draft(text), text=text, source_name=source_name, model=RULES_MODEL
        )


def get_draft_reader(request: Request) -> DraftReader:
    """FastAPI dependency (tests override it): one reader per app, built on first use."""
    state = request.app.state
    reader: DraftReader | None = getattr(state, "draft_reader", None)
    if reader is None:
        reader = DraftReader.from_settings(state.settings)
        state.draft_reader = reader
    return reader
