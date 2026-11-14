"""API models of background AI jobs and of the in-app assistant.

Purpose: Describe WBS generate/validate jobs as the client polls them, and the assistant's question
and answer, with limits on what a client may send.
How it fits: Used by app/api/v1/ai.py and app/api/v1/assistant.py; jobs are stored by
app/repositories/ai_jobs.py and run by app/jobs/ai_jobs.py; layer: domain.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.domain.common import AiJobKind, CamelModel, InputModel, JobStatus


class AiJob(CamelModel):
    """A background AI job (WBS generate or validate) with its status and result."""

    id: uuid.UUID
    project_id: uuid.UUID
    kind: AiJobKind
    status: JobStatus
    # The model that did the work; 'template' / 'rules' when the deterministic fallback ran.
    model: str | None = None
    error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AssistantMessageIn(InputModel):
    """One turn of the assistant conversation sent by the client."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class AssistantRequest(InputModel):
    """Body of the assistant endpoint: the page the user is on and the conversation so far."""

    project_id: uuid.UUID | None = None
    page: str = Field(max_length=40)
    messages: list[AssistantMessageIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _ends_with_a_question(self) -> AssistantRequest:
        """Require the conversation to end with a non-empty user message."""
        last = self.messages[-1]
        if last.role != "user" or not last.content:
            raise ValueError("the last message must be a non-empty user message")
        return self


class AssistantReply(CamelModel):
    """The assistant's answer."""

    reply: str
