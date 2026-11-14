from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, StringConstraints, model_validator

from app.domain.common import CamelModel, InputModel, ItemOrigin

NodeName = Annotated[str, StringConstraints(min_length=1, max_length=300)]
NodeDescription = Annotated[str, StringConstraints(max_length=5000)]
DurationDays = Annotated[int, Field(ge=0, le=3650)]


class EstimateSource(StrEnum):
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
    skill_id: uuid.UUID
    name: str
    min_proficiency: int


class WbsNode(CamelModel):
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
    parent_id: uuid.UUID | None = None
    name: NodeName
    duration_days: DurationDays | None = None
    owner_employee_id: uuid.UUID | None = None
    description: NodeDescription | None = None

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_days": self.duration_days,
            "owner_employee_id": self.owner_employee_id,
            "description": self.description or None,
        }


class WbsNodeUpdate(InputModel):
    """Partial update: only fields present in the body are changed."""

    name: NodeName | None = None
    duration_days: DurationDays | None = None
    owner_employee_id: uuid.UUID | None = None
    description: NodeDescription | None = None

    @model_validator(mode="after")
    def _validate(self) -> WbsNodeUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Fields cannot be null: name")
        return self

    def changes(self) -> dict[str, Any]:
        data = self.model_dump(exclude_unset=True, by_alias=False)
        if data.get("description") == "":
            data["description"] = None
        return data


class WbsConfirmEstimates(InputModel):
    """Which AI estimates to confirm; all of the project's when omitted."""

    node_ids: Annotated[list[uuid.UUID], Field(max_length=1000)] | None = None


class WbsMove(InputModel):
    position: Annotated[int, Field(ge=0)]


def reorder(sibling_ids: list[uuid.UUID], node_id: uuid.UUID, position: int) -> list[uuid.UUID]:
    """Siblings in their new order, with `node_id` at index `position` (clamped)."""
    rest = [s for s in sibling_ids if s != node_id]
    index = min(max(position, 0), len(rest))
    return [*rest[:index], node_id, *rest[index:]]
