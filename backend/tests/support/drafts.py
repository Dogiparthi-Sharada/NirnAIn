"""Fake Claude client for the project-draft reader.

Purpose: Let draft and log tests choose Claude's answer (or failure) without an API key.
How it fits: Used by test_drafts.py and tests/documents/test_log_redaction.py with
`app.drafts.reader.DraftReader`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from app.ai.llm import LLM, Usage
from app.drafts import ai as draft_ai


# --------------------------------------------------------------------------- endpoint
class FakeDraftLLM(LLM):
    """Configured LLM whose structured answer is fixed by the test."""

    def __init__(self, answer: draft_ai.AiDraft | Exception) -> None:
        """Answer every call with `answer`, or raise it when it is an exception."""
        super().__init__(client=None, model="claude-test")
        self.answer = answer

    @property
    def configured(self) -> bool:
        """Report a configured key, so the reader uses Claude."""
        return True

    def parse(self, **kwargs: object) -> tuple[draft_ai.AiDraft, Usage]:  # type: ignore[override]
        """Return the fixed draft with token usage, or raise the fixed error."""
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer, Usage(10, 10)
