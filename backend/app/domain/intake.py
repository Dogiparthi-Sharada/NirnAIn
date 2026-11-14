"""AI intake schemas: intake runs, scope items and uploaded source documents."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.domain.common import (
    CamelModel,
    DocumentKind,
    InputModel,
    ItemOrigin,
    JobStatus,
    ScopeCategory,
    ScopeStatus,
)


class ScopeItem(CamelModel):
    id: uuid.UUID
    project_id: uuid.UUID
    category: ScopeCategory
    text: str
    status: ScopeStatus
    origin: ItemOrigin
    confidence: float | None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class IntakeRun(CamelModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: JobStatus
    model: str | None
    items_extracted: int | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class IntakeRunCreate(InputModel):
    """Start an intake run; `text` (pasted notes) may be empty when an upload was read."""

    text: str = Field(default="", max_length=50_000)


class ScopeItemUpdate(InputModel):
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    status: ScopeStatus | None = None


class ScopeItemCreate(InputModel):
    category: ScopeCategory
    text: str = Field(min_length=1, max_length=2000)


class SourceDocument(CamelModel):
    """An uploaded intake document: only its extracted text is stored (`chars` long)."""

    id: uuid.UUID
    project_id: uuid.UUID
    kind: DocumentKind
    title: str
    mime_type: str | None
    size_bytes: int | None
    extraction_status: JobStatus
    extraction_error: str | None
    chars: int
    created_at: datetime
