"""API models and pure rules of the planning steps: constraints, resources and dependencies.

Purpose: Validate the constraint set (budget, deadline, policies, answers to the plan-changing
questions), describe the resource pool and dependencies, and compute the planning window, average
load, skill coverage and dependency cycles.
How it fits: Used by app/api/v1/planning.py and app/repositories/planning.py; layer: domain.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from app.domain.common import CamelModel, ConstraintMode, DependencyType, InputModel, ItemOrigin
from app.domain.projects import BudgetCents

PLANNING_FALLBACK_WEEKS = 12

MilestoneName = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Pct = Annotated[float, Field(ge=0, le=100)]
WorkingDays = Annotated[int, Field(ge=1, le=7)]
LagDays = Annotated[int, Field(ge=-365, le=365)]


# --------------------------------------------------------------------------- constraints
SpecialistAnswer = Literal["yes", "no", "unknown"]
DeadlineAnswer = Literal["hard", "soft", "unknown"]

# Answers to the questions that change the plan (null = not answered yet) and contractor terms.
ANSWER_FIELDS = (
    "specialist_answer",
    "deadline_answer",
    "specialist_rate_confirmed",
    "contractor_rate_cents",
    "allow_contractors",
)


class Milestone(CamelModel):
    """A dated milestone, optionally tied to a WBS node, with its policy."""

    id: uuid.UUID
    name: str
    due_date: date
    wbs_node_id: uuid.UUID | None
    mode: ConstraintMode


class ConstraintSet(CamelModel):
    """The project's saved constraints and answers to the plan-changing questions."""

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
    specialist_answer: SpecialistAnswer | None = None
    deadline_answer: DeadlineAnswer | None = None
    specialist_rate_confirmed: bool = False
    contractor_rate_cents: int | None = None
    allow_contractors: bool = True


class MilestoneInput(InputModel):
    """A milestone in the constraints body; without `id` it is new."""

    id: uuid.UUID | None = None
    name: MilestoneName
    due_date: date
    wbs_node_id: uuid.UUID | None = None
    mode: ConstraintMode


class ConstraintSetInput(InputModel):
    """Body of `PUT …/constraints`: the whole constraint set."""

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
    # Optional so older clients keep working: fields left out keep their saved values.
    specialist_answer: SpecialistAnswer | None = None
    deadline_answer: DeadlineAnswer | None = None
    specialist_rate_confirmed: bool | None = None
    contractor_rate_cents: BudgetCents | None = None
    allow_contractors: bool | None = None

    @model_validator(mode="after")
    def _validate(self) -> ConstraintSetInput:
        """Reject a deadline before the start date and duplicate milestone ids."""
        if (
            self.start_date is not None
            and self.must_finish_by is not None
            and self.must_finish_by < self.start_date
        ):
            raise ValueError("mustFinishBy must be on or after startDate")
        ids = [milestone.id for milestone in self.milestones if milestone.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Milestone ids must be unique")
        return self

    def values(self) -> dict[str, Any]:
        """Columns to save. The deadline policy follows the deadline answer once it is given,
        and the specialist answer decides whether outside contractors may be used."""
        values = self.model_dump(exclude={"milestones", *ANSWER_FIELDS}, by_alias=False)
        for name in ANSWER_FIELDS:
            if name in self.model_fields_set:
                values[name] = getattr(self, name)
        if values.get("specialist_rate_confirmed") is None:
            values.pop("specialist_rate_confirmed", None)
        if values.get("allow_contractors") is None:
            values.pop("allow_contractors", None)
        if self.deadline_answer is not None:
            soft = self.deadline_answer == "soft"
            values["deadline_mode"] = ConstraintMode.SOFT if soft else ConstraintMode.HARD
        if self.specialist_answer is not None and "allow_contractors" not in values:
            values["allow_contractors"] = self.specialist_answer == "yes"
        return values


# --------------------------------------------------------------------------- resources
class EmployeeSkill(CamelModel):
    """A skill an employee holds and at which proficiency."""

    skill_id: uuid.UUID
    name: str
    proficiency: int


class PoolEmployee(CamelModel):
    """An employee of the organization that the project can draw on."""

    employee_id: uuid.UUID
    full_name: str
    role_title: str | None
    weekly_capacity_hours: float
    hourly_rate_cents: int | None
    is_contractor: bool
    skills: list[EmployeeSkill]


class CapacityEntry(PoolEmployee):
    """A pool employee with the hours already allocated to them."""

    allocated_hours: float


class SkillCoverage(CamelModel):
    """Whether a skill the project needs is held by someone on the team."""

    skill_id: uuid.UUID
    name: str
    required: bool
    available: bool


# --------------------------------------------------------------------------- dependencies
class Dependency(CamelModel):
    """A link between two work packages, with readable labels."""

    id: uuid.UUID
    predecessor_id: uuid.UUID
    successor_id: uuid.UUID
    predecessor_label: str
    successor_label: str
    type: DependencyType
    lag_days: int
    origin: ItemOrigin


class DependencyCreate(InputModel):
    """Body to add a dependency between two work packages."""

    predecessor_id: uuid.UUID
    successor_id: uuid.UUID
    type: DependencyType = DependencyType.FS
    lag_days: LagDays = 0

    @model_validator(mode="after")
    def _validate(self) -> DependencyCreate:
        """Reject a work package that depends on itself."""
        if self.predecessor_id == self.successor_id:
            raise ValueError("A task cannot depend on itself")
        return self


class DependencyUpdate(InputModel):
    """Body to change a dependency's type or lag."""

    type: DependencyType | None = None
    lag_days: LagDays | None = None

    @model_validator(mode="after")
    def _validate(self) -> DependencyUpdate:
        """Reject an empty body and nulls."""
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        nulls = sorted(
            field_name for field_name in self.model_fields_set if getattr(self, field_name) is None
        )
        if nulls:
            raise ValueError(f"Fields cannot be null: {', '.join(nulls)}")
        return self

    def changes(self) -> dict[str, Any]:
        """Return the columns the body sets."""
        return self.model_dump(exclude_unset=True, by_alias=False)


# --------------------------------------------------------------------------- computations
def _monday(day: date) -> date:
    """Return the Monday of the week the day falls in."""
    return day - timedelta(days=day.weekday())


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
    out.sort(
        key=lambda coverage: (
            not coverage.required,
            coverage.name.casefold(),
            str(coverage.skill_id),
        )
    )
    return out


def cycle_path(
    links: Iterable[tuple[uuid.UUID, uuid.UUID]], predecessor: uuid.UUID, successor: uuid.UUID
) -> list[uuid.UUID] | None:
    """The cycle a new link `predecessor` → `successor` would close, as nodes from the
    predecessor back to it ([W05, W01, W05]); None when the link closes none.

    `links` are the existing (predecessor, successor) pairs. Breadth-first, so the cycle
    named is a shortest one.
    """
    following: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for before, after in links:
        following[before].append(after)
    reached_from: dict[uuid.UUID, uuid.UUID | None] = {successor: None}
    queue = deque([successor])
    while queue:
        current = queue.popleft()
        if current == predecessor:
            path: list[uuid.UUID] = []
            step: uuid.UUID | None = current
            while step is not None:
                path.append(step)
                step = reached_from[step]
            return [predecessor, *reversed(path)]
        for after in following[current]:
            if after not in reached_from:
                reached_from[after] = current
                queue.append(after)
    return None
