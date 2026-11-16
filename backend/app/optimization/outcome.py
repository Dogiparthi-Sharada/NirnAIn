"""Solver-side types: the resources tasks are assigned to, and what a solve produces.

Purpose: Give the solver, the report and the remedies one shared vocabulary for "who can do the
work" (team members and contractor profiles) and for a solved plan or its conflicts.
How it fits: Built by resources.py and solver.py; read by explain.py, remedies.py and the job
runner; layer: optimization.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.optimization.model import Task

RunStatus = Literal["optimal", "feasible", "infeasible", "timeout", "failed"]
ResourceKind = Literal["employee", "contractor"]


@dataclass(frozen=True, slots=True)
class Resource:
    """Something a task can be assigned to: a team member or a contractor profile.

    A contractor resource stands for "as many people of this profile as
    needed", so it has no capacity limit (`resources.build_resources`).
    """

    id: str
    name: str
    kind: ResourceKind
    weekly_capacity_hours: float
    rate_cents: int
    skills: Mapping[str, int]
    employee_id: str | None = None
    # Billed per hour worked above their capacity (= rate_cents for contractors).
    overtime_rate_cents: int = 0

    def proficiency(self, skill_id: str) -> int:
        """Return the resource's level in a skill (0 when they don't have it)."""
        return self.skills.get(skill_id, 0)

    def skill_gap(self, task: Task) -> int:
        """Total proficiency levels missing to do `task` (0 = fully qualified)."""
        return sum(
            max(0, requirement.min_proficiency - self.proficiency(requirement.skill_id))
            for requirement in task.skills
        )


def qualified(resource: Resource, task: Task) -> bool:
    """Tell whether the resource meets every skill requirement of the task."""
    return resource.skill_gap(task) == 0


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """A task's place in a plan: working-day start (inclusive), end (exclusive) and who does it."""

    task_id: str
    start: int  # working-day index from the project start (inclusive)
    end: int  # exclusive
    resource_id: str | None  # None = unassigned (baseline only)


@dataclass(frozen=True, slots=True)
class Conflict:
    """One hard constraint in an infeasible core, in plain English.

    `kind` is its constraint group (deadline, milestone, budget, labor, skill, capacity or
    dependency; empty for the generic text) and `brief` restates its fact as a clause ("the
    dependency chain alone runs until Aug 23, 2027"), so the remedies can say which blockers an
    option leaves in place. Only name and explanation are stored with the run.
    """

    name: str
    explanation: str
    kind: str = field(default="", compare=False)
    brief: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class Plan:
    """A complete schedule: every task's scheduled place."""

    tasks: Mapping[str, ScheduledTask]

    @property
    def makespan(self) -> int:
        """Return the working day the last task ends (0 for an empty plan)."""
        return max((scheduled.end for scheduled in self.tasks.values()), default=0)


@dataclass(frozen=True, slots=True)
class SolveOutcome:
    """What one solve produced: status, timings, the baseline, the plan or the conflicts."""

    status: RunStatus
    solver: str  # e.g. "OR-Tools CP-SAT 9.15.6755"
    solver_version: str
    wall_seconds: float
    resources: tuple[Resource, ...]
    baseline: Plan
    plan: Plan | None = None
    objective: float | None = None
    best_bound: float | None = None
    hit_time_limit: bool = False
    conflicts: tuple[Conflict, ...] = ()
    message: str | None = None  # user-readable, for timeout / failed

    @property
    def gap(self) -> float | None:
        """Return the relative gap between objective and best bound (None if unknown)."""
        if self.objective is None or self.best_bound is None:
            return None
        if self.objective == 0:
            return 0.0
        return max(0.0, abs(self.objective - self.best_bound) / abs(self.objective))
