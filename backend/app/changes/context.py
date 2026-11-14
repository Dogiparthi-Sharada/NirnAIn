"""What a change is read against: the project's current inputs and the plan in force."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domain.common import SolverStatus


@dataclass(frozen=True, slots=True)
class TaskRef:
    """A work package a change can target (level 3, or a level-2 node without children)."""

    id: uuid.UUID
    code: str
    name: str
    duration_days: int | None
    owner_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class PersonRef:
    """Someone on the project and the hours a week they give it."""

    id: uuid.UUID
    name: str
    hours_per_week: float


@dataclass(frozen=True, slots=True)
class ChangeContext:
    """Inputs and plan in force at the time a change is read; pure data for the readers."""

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
    # Skill names the project needs: what a newcomer named in the notes may bring.
    skills: tuple[str, ...] = ()

    def task(self, task_id: uuid.UUID) -> TaskRef | None:
        """The work package with this id, if it is part of the project."""
        return next((task for task in self.tasks if task.id == task_id), None)

    def person(self, person_id: uuid.UUID) -> PersonRef | None:
        """The team member with this id, if on the project."""
        return next((person for person in self.people if person.id == person_id), None)

    @property
    def plan_label(self) -> str:
        """User-facing name of the plan in force ("Plan vN")."""
        return f"Plan v{self.plan_version}" if self.plan_version else "the current plan"


@dataclass(frozen=True, slots=True)
class RunMeta:
    """A run's KPIs and its frozen input snapshot (start date, constraints, tasks)."""

    id: uuid.UUID
    project_id: uuid.UUID
    status: SolverStatus
    version: int | None
    expected_cost_cents: int | None
    completion_date: date | None
    snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlanRow:
    """One planned assignment; weeks count from the Monday of the run's planning start."""

    wbs_node_id: uuid.UUID
    employee_id: uuid.UUID | None
    contractor_label: str | None
    start_week: int
    end_week: int


@dataclass(frozen=True, slots=True)
class NodeRow:
    """A WBS node with its derived code, for labels and deliverable grouping."""

    id: uuid.UUID
    parent_id: uuid.UUID | None
    level: int
    code: str
    name: str
