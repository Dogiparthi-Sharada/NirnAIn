from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Annotated, Any

from pydantic import Field, StringConstraints, model_validator

from app.domain.common import CamelModel, ConstraintMode, DependencyType, InputModel, ItemOrigin
from app.domain.projects import BudgetCents

PLANNING_FALLBACK_WEEKS = 12

MilestoneName = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Pct = Annotated[float, Field(ge=0, le=100)]
WorkingDays = Annotated[int, Field(ge=1, le=7)]
LagDays = Annotated[int, Field(ge=-365, le=365)]


# --------------------------------------------------------------------------- constraints
class Milestone(CamelModel):
    id: uuid.UUID
    name: str
    due_date: date
    wbs_node_id: uuid.UUID | None
    mode: ConstraintMode


class ConstraintSet(CamelModel):
    budget_total_cents: int | None
    contingency_pct: float
    labor_cap_cents: int | None
    infra_cap_cents: int | None
    budget_mode: ConstraintMode
    start_date: date | None
    must_finish_by: date | None
    deadline_mode: ConstraintMode
    working_days_per_week: int
    capacity_mode: ConstraintMode
    skill_mode: ConstraintMode
    milestones: list[Milestone]


class MilestoneInput(InputModel):
    id: uuid.UUID | None = None
    name: MilestoneName
    due_date: date
    wbs_node_id: uuid.UUID | None = None
    mode: ConstraintMode


class ConstraintSetInput(InputModel):
    budget_total_cents: BudgetCents | None
    contingency_pct: Pct
    labor_cap_cents: BudgetCents | None
    infra_cap_cents: BudgetCents | None
    budget_mode: ConstraintMode
    start_date: date | None
    must_finish_by: date | None
    deadline_mode: ConstraintMode
    working_days_per_week: WorkingDays
    capacity_mode: ConstraintMode
    skill_mode: ConstraintMode
    milestones: Annotated[list[MilestoneInput], Field(max_length=200)]

    @model_validator(mode="after")
    def _validate(self) -> ConstraintSetInput:
        if (
            self.start_date is not None
            and self.must_finish_by is not None
            and self.must_finish_by < self.start_date
        ):
            raise ValueError("mustFinishBy must be on or after startDate")
        ids = [m.id for m in self.milestones if m.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Milestone ids must be unique")
        return self

    def values(self) -> dict[str, Any]:
        return self.model_dump(exclude={"milestones"}, by_alias=False)


# --------------------------------------------------------------------------- resources
class EmployeeSkill(CamelModel):
    skill_id: uuid.UUID
    name: str
    proficiency: int


class PoolEmployee(CamelModel):
    employee_id: uuid.UUID
    full_name: str
    role_title: str | None
    weekly_capacity_hours: float
    hourly_rate_cents: int | None
    is_contractor: bool
    skills: list[EmployeeSkill]


class CapacityEntry(PoolEmployee):
    allocated_hours: float


class SkillCoverage(CamelModel):
    skill_id: uuid.UUID
    name: str
    required: bool
    available: bool


# --------------------------------------------------------------------------- dependencies
class Dependency(CamelModel):
    id: uuid.UUID
    predecessor_id: uuid.UUID
    successor_id: uuid.UUID
    predecessor_label: str
    successor_label: str
    type: DependencyType
    lag_days: int
    origin: ItemOrigin


class DependencyCreate(InputModel):
    predecessor_id: uuid.UUID
    successor_id: uuid.UUID
    type: DependencyType = DependencyType.FS
    lag_days: LagDays = 0

    @model_validator(mode="after")
    def _validate(self) -> DependencyCreate:
        if self.predecessor_id == self.successor_id:
            raise ValueError("A task cannot depend on itself")
        return self


class DependencyUpdate(InputModel):
    type: DependencyType | None = None
    lag_days: LagDays | None = None

    @model_validator(mode="after")
    def _validate(self) -> DependencyUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        nulls = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
        if nulls:
            raise ValueError(f"Fields cannot be null: {', '.join(nulls)}")
        return self

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True, by_alias=False)


# --------------------------------------------------------------------------- computations
def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def planning_weeks(start: date | None, end: date | None, today: date) -> list[date]:
    """Mondays of the planning window (the next 12 weeks when it has no usable end)."""
    first = start or today
    if end is None or end < first:
        end = _monday(first) + timedelta(weeks=PLANNING_FALLBACK_WEEKS, days=-1)
    weeks = []
    monday = _monday(first)
    while monday <= end:
        weeks.append(monday)
        monday += timedelta(weeks=1)
    return weeks


def average_weekly_hours(total_hours: float, week_count: int) -> float:
    """Average over the window (weeks without allocations count as 0), to the nearest 0.5."""
    if week_count <= 0:
        return 0.0
    return math.floor(total_hours / week_count * 2 + 0.5) / 2


def skill_coverage(
    required: Mapping[uuid.UUID, tuple[str, int]],
    held: Mapping[uuid.UUID, tuple[str, int]],
) -> list[SkillCoverage]:
    """Coverage of required skills (name, max min_proficiency) by held skills (name, max level).

    Skills nobody requires are listed as not required and available.
    """
    out = [
        SkillCoverage(
            skill_id=skill_id,
            name=name,
            required=True,
            available=skill_id in held and held[skill_id][1] >= level,
        )
        for skill_id, (name, level) in required.items()
    ]
    out += [
        SkillCoverage(skill_id=skill_id, name=name, required=False, available=True)
        for skill_id, (name, _) in held.items()
        if skill_id not in required
    ]
    out.sort(key=lambda c: (not c.required, c.name.casefold(), str(c.skill_id)))
    return out
