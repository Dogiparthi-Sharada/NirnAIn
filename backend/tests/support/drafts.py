"""Moved test support."""

from __future__ import annotations

from app.ai.llm import LLM, Usage
from app.drafts import ai as draft_ai


# --------------------------------------------------------------------------- endpoint
class FakeDraftLLM(LLM):
    def __init__(self, answer: draft_ai.AiDraft | Exception) -> None:
        super().__init__(client=None, model="claude-test")
        self.answer = answer

    @property
    def configured(self) -> bool:
        return True

    def parse(self, **kwargs: object) -> tuple[draft_ai.AiDraft, Usage]:  # type: ignore[override]
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer, Usage(10, 10)
