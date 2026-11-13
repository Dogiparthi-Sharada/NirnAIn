from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.domain.common import AiJobKind, CamelModel, InputModel, JobStatus


class AiJob(CamelModel):
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
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class AssistantRequest(InputModel):
    project_id: uuid.UUID | None = None
    page: str = Field(max_length=40)
    messages: list[AssistantMessageIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _ends_with_a_question(self) -> AssistantRequest:
        last = self.messages[-1]
        if last.role != "user" or not last.content:
            raise ValueError("the last message must be a non-empty user message")
        return self


class AssistantReply(CamelModel):
    reply: str
