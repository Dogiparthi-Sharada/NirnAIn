"""What a change is read against: the project's current inputs and the plan in force."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domain.common import SolverStatus


@dataclass(frozen=True, slots=True)
class TaskRef:
    id: uuid.UUID
    code: str
    name: str
    duration_days: int | None
    owner_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class PersonRef:
    id: uuid.UUID
    name: str
    hours_per_week: float


@dataclass(frozen=True, slots=True)
class ChangeContext:
    budget_cents: int | None
    deadline: date | None
    start: date | None
    tasks: tuple[TaskRef, ...] = ()
    people: tuple[PersonRef, ...] = ()
    # (predecessor, successor) WBS node ids.
    edges: tuple[tuple[uuid.UUID, uuid.UUID], ...] = ()
    plan_version: int | None = None
    plan_cost_cents: int | None = None
    plan_finish: date | None = None
    today: date = field(default_factory=date.today)

    def task(self, task_id: uuid.UUID) -> TaskRef | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def person(self, person_id: uuid.UUID) -> PersonRef | None:
        return next((p for p in self.people if p.id == person_id), None)

    @property
    def plan_label(self) -> str:
        return f"Plan v{self.plan_version}" if self.plan_version else "the current plan"


@dataclass(frozen=True, slots=True)
class RunMeta:
    id: uuid.UUID
    project_id: uuid.UUID
    status: SolverStatus
    version: int | None
    expected_cost_cents: int | None
    completion_date: date | None
    snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlanRow:
    wbs_node_id: uuid.UUID
    employee_id: uuid.UUID | None
    contractor_label: str | None
    start_week: int
    end_week: int


@dataclass(frozen=True, slots=True)
class NodeRow:
    id: uuid.UUID
    parent_id: uuid.UUID | None
    level: int
    code: str
    name: str
