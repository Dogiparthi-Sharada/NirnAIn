"""API models of project drafts: details read from a transcript, document or pasted notes.

Purpose: A draft is never stored. Every field carries where it came from so the PM can check it
before creating the project: `stated` (with the verbatim quote), `inferred` or `missing`.
How it fits: Returned by app/api/v1/drafts.py; filled by app/drafts (Claude or the rule-based
reader); layer: domain.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from app.domain.common import CamelModel, InputModel

DraftStatus = Literal["stated", "inferred", "missing"]

# Same ceiling as an intake run's notes, so pasted notes can be analyzed as-is.
MAX_DRAFT_TEXT = 50_000
RULES_MODEL = "rules"


class DraftField(CamelModel):
    """One value read from the source, with the sentence it came from and how sure we are."""

    value: str | date | int | None = None
    quote: str | None = None
    status: DraftStatus = "missing"


class TextField(DraftField):
    """Name, description, department or sponsor."""

    value: str | None = None


class DateField(DraftField):
    """Start or target end date (ISO on the wire)."""

    value: date | None = None


class MoneyField(DraftField):
    """Integer cents."""

    value: int | None = None


class ProjectDraftFields(CamelModel):
    """The project details a source can supply; anything not supplied stays missing."""

    name: TextField = Field(default_factory=TextField)
    description: TextField = Field(default_factory=TextField)
    start_date: DateField = Field(default_factory=DateField)
    target_end_date: DateField = Field(default_factory=DateField)
    budget_cents: MoneyField = Field(default_factory=MoneyField)
    department: TextField = Field(default_factory=TextField)
    sponsor: TextField = Field(default_factory=TextField)


class ProjectDraft(CamelModel):
    """Response of `POST /orgs/{orgId}/project-drafts/extract`."""

    fields: ProjectDraftFields
    # The text that was read (file text or the pasted notes).
    text: str
    source_name: str
    # Claude model id, or "rules" when the deterministic reader filled the draft.
    model: str


class ProjectDraftTextIn(InputModel):
    """JSON body for pasted notes (a file comes as multipart instead)."""

    text: str = Field(min_length=1, max_length=MAX_DRAFT_TEXT)
    source_name: str | None = Field(default=None, max_length=200)
