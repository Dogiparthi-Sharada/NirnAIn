"""API models of the work breakdown structure (WBS) and its built-in checks.

Purpose: Describe WBS nodes (deliverables and work packages), how they are created, edited, moved
and confirmed, and whether each duration estimate is AI's or the PM's.
How it fits: Used by app/api/v1/wbs.py and app/repositories/wbs.py (checks:
app/repositories/wbs_checks.py); layer: domain.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from app.domain.common import CamelModel, InputModel, ItemOrigin
from app.domain.team import SkillNames

NodeName = Annotated[str, StringConstraints(min_length=1, max_length=300)]
NodeDescription = Annotated[str, StringConstraints(max_length=5000)]
DurationDays = Annotated[int, Field(ge=0, le=3650)]


class EstimateSource(StrEnum):
    """Who gave a duration estimate: AI or the PM."""

    AI = "ai"
    YOURS = "yours"


def estimate_state(
    origin: str, duration_days: int | None, confirmed_at: datetime | None
) -> tuple[bool, EstimateSource | None]:
    """(estimateConfirmed, estimateSource). An AI estimate counts only once a person confirms it;
    a duration typed in by a person is theirs, so it is confirmed by entering it."""
    if duration_days is None:
        return confirmed_at is not None, None
    if origin == ItemOrigin.AI:
        return confirmed_at is not None, EstimateSource.AI
    return True, EstimateSource.YOURS


class WbsSkill(CamelModel):
    """A skill a work package needs and at which minimum proficiency."""

    skill_id: uuid.UUID
    name: str
    min_proficiency: int


class WbsNode(CamelModel):
    """A node of the WBS (deliverable or work package) as the API returns it."""

    id: uuid.UUID
    parent_id: uuid.UUID | None
    level: int
    sort_order: int
    code: str
    name: str
    description: str | None
    duration_days: int | None
    effort_hours: float | None
    owner_employee_id: uuid.UUID | None
    owner_name: str | None
    origin: ItemOrigin
    scope_item_id: uuid.UUID | None
    skills: list[WbsSkill]
    estimate_confirmed: bool = False
    estimate_source: EstimateSource | None = None


class WbsNodeCreate(InputModel):
    """Body to add a WBS node under a parent (or at the top level)."""

    parent_id: uuid.UUID | None = None
    name: NodeName
    duration_days: DurationDays | None = None
    owner_employee_id: uuid.UUID | None = None
    description: NodeDescription | None = None

    def values(self) -> dict[str, Any]:
        """Return the columns to insert; an empty description is stored as null."""
        return {
            "name": self.name,
            "duration_days": self.duration_days,
            "owner_employee_id": self.owner_employee_id,
            "description": self.description or None,
        }


class WbsNodeUpdate(InputModel):
    """Partial update: only fields present in the body are changed.

    `skills` (names) replaces the skills the work package needs; new ones are
    needed at proficiency 3, kept ones keep theirs (round-2 decision D6).
    """

    name: NodeName | None = None
    duration_days: DurationDays | None = None
    owner_employee_id: uuid.UUID | None = None
    description: NodeDescription | None = None
    skills: SkillNames | None = None

    @model_validator(mode="after")
    def _validate(self) -> WbsNodeUpdate:
        """Reject an empty body and nulls for the name or skills."""
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for field_name in ("name", "skills"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"Fields cannot be null: {field_name}")
        return self

    def changes(self) -> dict[str, Any]:
        """The node's own columns to update (skills are stored separately)."""
        data = self.model_dump(exclude_unset=True, exclude={"skills"}, by_alias=False)
        if data.get("description") == "":
            data["description"] = None
        return data


class WbsConfirmEstimates(InputModel):
    """Which AI estimates to confirm; all of the project's when omitted."""

    node_ids: Annotated[list[uuid.UUID], Field(max_length=1000)] | None = None


class WbsMove(InputModel):
    """Body to move a node to a new position among its siblings."""

    position: Annotated[int, Field(ge=0)]


class WbsCheckIssue(CamelModel):
    """One finding of the built-in checks, shaped like the validate job's issues. Type
    'info' is a note for later steps (e.g. the skills step 5 will match), not an issue."""

    type: Literal["missing", "duplicate", "warning", "info"]
    text: str


class WbsChecks(CamelModel):
    """The free built-in checks (no AI) on the current WBS, team and constraints.

    `wbs_changed_at` is when the WBS nodes last changed (None without nodes): the builder
    compares it with the last AI review to say when that review is out of date.
    """

    issues: list[WbsCheckIssue]
    checked_at: datetime
    wbs_changed_at: datetime | None


def reorder(sibling_ids: list[uuid.UUID], node_id: uuid.UUID, position: int) -> list[uuid.UUID]:
    """Siblings in their new order, with `node_id` at index `position` (clamped)."""
    rest = [sibling_id for sibling_id in sibling_ids if sibling_id != node_id]
    index = min(max(position, 0), len(rest))
    return [*rest[:index], node_id, *rest[index:]]
