"""Planning input/output types and the frozen `input_snapshot` format.

The snapshot is what the API freezes into `optimization_runs.input_snapshot`
when a run is queued, so a run is reproducible even if the WBS or team
changes while it is solving. `PlanningInput.to_snapshot()` and
`PlanningInput.from_snapshot()` are exact inverses.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, cast

SNAPSHOT_VERSION = 1

DependencyType = Literal["FS", "SS", "FF", "SF"]
ConstraintMode = Literal["hard", "soft"]
RunStatus = Literal["optimal", "feasible", "infeasible", "timeout", "failed"]
ResourceKind = Literal["employee", "contractor"]

DEFAULT_HOURS_PER_DAY = 8.0
# People added in the app often have no rate yet. They are planned at a blended
# IT rate ($85/h): priced at $0 they would make every budget meaningless and
# attract all the work, and a team average is $0 when nobody has a rate. The
# confirmation sheet lists who is planned at this default.
DEFAULT_HOURLY_RATE_CENTS = 8_500
CONTRACTOR_WEEKLY_HOURS = 40.0
MAX_PROFICIENCY = 5


class SnapshotError(ValueError):
    """The snapshot is malformed (written by an incompatible version or corrupted)."""


# --------------------------------------------------------------------------- input
@dataclass(frozen=True, slots=True)
class SkillRequirement:
    skill_id: str
    skill_name: str
    min_proficiency: int


@dataclass(frozen=True, slots=True)
class Task:
    """A work package (leaf of the WBS) with a positive duration in working days."""

    id: str
    code: str
    name: str
    duration_days: int
    effort_hours: float | None = None
    owner_id: str | None = None
    skills: tuple[SkillRequirement, ...] = ()
    # Finished work (0 working days left): not scheduled again. It keeps the dates of
    # the plan in force (inclusive) when a re-plan knows them.
    done: bool = False
    applied_start: date | None = None
    applied_end: date | None = None

    @property
    def label(self) -> str:
        return f"{self.code} {self.name}"

    @property
    def hours(self) -> float:
        """Effort still to do, for cost and load: explicit effort, else 8 h per working
        day; none once the work is done."""
        if self.done:
            return 0.0
        if self.effort_hours is not None and self.effort_hours > 0:
            return self.effort_hours
        return self.duration_days * DEFAULT_HOURS_PER_DAY


@dataclass(frozen=True, slots=True)
class Employee:
    id: str
    name: str
    weekly_capacity_hours: float = 40.0
    hourly_rate_cents: int | None = None
    is_contractor: bool = False
    skills: Mapping[str, int] = field(default_factory=dict)  # skill_id -> proficiency 1..5


@dataclass(frozen=True, slots=True)
class Dependency:
    predecessor_id: str
    successor_id: str
    type: DependencyType = "FS"
    lag_days: int = 0


@dataclass(frozen=True, slots=True)
class Milestone:
    id: str
    name: str
    due_date: date
    mode: ConstraintMode = "hard"
    task_ids: tuple[str, ...] = ()  # leaf tasks under the milestone's WBS node


@dataclass(frozen=True, slots=True)
class Allocation:
    """Hours an employee is already committed to *other* projects in a week."""

    employee_id: str
    week_start: date  # a Monday
    hours: float


@dataclass(frozen=True, slots=True)
class Constraints:
    budget_total_cents: int | None = None
    labor_cap_cents: int | None = None
    budget_mode: ConstraintMode = "hard"
    budget_penalty: float = 1000.0  # objective cost per $1,000 over budget
    must_finish_by: date | None = None
    deadline_mode: ConstraintMode = "hard"
    deadline_penalty_per_day: float = 500.0  # objective cost per working day late
    working_days_per_week: int = 5
    capacity_mode: ConstraintMode = "soft"
    capacity_penalty: float = 100.0  # objective cost per hour of work above capacity (soft)
    skill_mode: ConstraintMode = "hard"
    allow_contractors: bool = True
    contractor_rate_cents: int | None = None


@dataclass(frozen=True, slots=True)
class PlanningInput:
    project_id: str
    project_name: str
    start_date: date
    tasks: tuple[Task, ...]
    employees: tuple[Employee, ...]
    dependencies: tuple[Dependency, ...] = ()
    constraints: Constraints = field(default_factory=Constraints)
    milestones: tuple[Milestone, ...] = ()
    allocations: tuple[Allocation, ...] = ()
    # Deliverable (level-2 WBS node) names by code, for grouping the timeline.
    deliverables: Mapping[str, str] = field(default_factory=dict)
    # The day the plan is made: unfinished work never starts before it (None: no anchor,
    # the plan may start on the start date even if that has passed).
    today: date | None = None

    # ------------------------------------------------------------------ snapshot
    def to_snapshot(self) -> dict[str, Any]:
        """The JSON frozen into `optimization_runs.input_snapshot` (see `from_snapshot`)."""
        constraints = self.constraints
        return {
            "version": SNAPSHOT_VERSION,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "start_date": self.start_date.isoformat(),
            "today": _iso(self.today),
            "tasks": [
                {
                    "id": task.id,
                    "code": task.code,
                    "name": task.name,
                    "duration_days": task.duration_days,
                    "effort_hours": task.effort_hours,
                    "owner_id": task.owner_id,
                    "skills": [
                        {
                            "skill_id": requirement.skill_id,
                            "skill_name": requirement.skill_name,
                            "min_proficiency": requirement.min_proficiency,
                        }
                        for requirement in task.skills
                    ],
                    "done": task.done,
                    "applied_start": _iso(task.applied_start),
                    "applied_end": _iso(task.applied_end),
                }
                for task in self.tasks
            ],
            "employees": [
                {
                    "id": employee.id,
                    "name": employee.name,
                    "weekly_capacity_hours": employee.weekly_capacity_hours,
                    "hourly_rate_cents": employee.hourly_rate_cents,
                    "is_contractor": employee.is_contractor,
                    "skills": dict(employee.skills),
                }
                for employee in self.employees
            ],
            "dependencies": [
                {
                    "predecessor_id": link.predecessor_id,
                    "successor_id": link.successor_id,
                    "type": link.type,
                    "lag_days": link.lag_days,
                }
                for link in self.dependencies
            ],
            "constraints": {
                "budget_total_cents": constraints.budget_total_cents,
                "labor_cap_cents": constraints.labor_cap_cents,
                "budget_mode": constraints.budget_mode,
                "budget_penalty": constraints.budget_penalty,
                "must_finish_by": _iso(constraints.must_finish_by),
                "deadline_mode": constraints.deadline_mode,
                "deadline_penalty_per_day": constraints.deadline_penalty_per_day,
                "working_days_per_week": constraints.working_days_per_week,
                "capacity_mode": constraints.capacity_mode,
                "capacity_penalty": constraints.capacity_penalty,
                "skill_mode": constraints.skill_mode,
                "allow_contractors": constraints.allow_contractors,
                "contractor_rate_cents": constraints.contractor_rate_cents,
            },
            "milestones": [
                {
                    "id": milestone.id,
                    "name": milestone.name,
                    "due_date": milestone.due_date.isoformat(),
                    "mode": milestone.mode,
                    "task_ids": list(milestone.task_ids),
                }
                for milestone in self.milestones
            ],
            "allocations": [
                {
                    "employee_id": booking.employee_id,
                    "week_start": booking.week_start.isoformat(),
                    "hours": booking.hours,
                }
                for booking in self.allocations
            ],
            "deliverables": dict(self.deliverables),
        }

    @classmethod
    def from_snapshot(cls, data: Mapping[str, Any]) -> PlanningInput:
        try:
            return _parse_snapshot(data)
        except SnapshotError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise SnapshotError(f"Invalid input snapshot: {type(exc).__name__}: {exc}") from exc


def _mode(value: Any) -> ConstraintMode:
    if value not in ("hard", "soft"):
        raise SnapshotError(f"Invalid constraint mode {value!r}")
    return cast(ConstraintMode, value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _opt_date(value: Any) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _penalty(value: Any, default: float) -> float:
    # The columns are unconstrained `real`s: a negative or NaN weight would turn
    # a penalty into a reward, so fall back to the default.
    number = default if value is None else float(value)
    return number if math.isfinite(number) and number >= 0 else default


def _parse_task(entry: Mapping[str, Any]) -> Task:
    return Task(
        id=str(entry["id"]),
        code=str(entry["code"]),
        name=str(entry["name"]),
        duration_days=int(entry["duration_days"]),
        effort_hours=None if entry.get("effort_hours") is None else float(entry["effort_hours"]),
        owner_id=None if entry.get("owner_id") is None else str(entry["owner_id"]),
        skills=tuple(
            SkillRequirement(
                skill_id=str(requirement["skill_id"]),
                skill_name=str(requirement["skill_name"]),
                min_proficiency=int(requirement["min_proficiency"]),
            )
            for requirement in entry.get("skills", ())
        ),
        done=bool(entry.get("done", False)),
        applied_start=_opt_date(entry.get("applied_start")),
        applied_end=_opt_date(entry.get("applied_end")),
    )


def _parse_snapshot(data: Mapping[str, Any]) -> PlanningInput:
    if data.get("version") != SNAPSHOT_VERSION:
        raise SnapshotError(f"Unsupported input snapshot version {data.get('version')!r}")
    stored = data.get("constraints") or {}
    per_week = int(stored.get("working_days_per_week", 5))
    if not 1 <= per_week <= 7:
        raise SnapshotError("working_days_per_week must be between 1 and 7")
    constraints = Constraints(
        budget_total_cents=_opt_int(stored.get("budget_total_cents")),
        labor_cap_cents=_opt_int(stored.get("labor_cap_cents")),
        budget_mode=_mode(stored.get("budget_mode", "hard")),
        budget_penalty=_penalty(stored.get("budget_penalty"), 1000.0),
        must_finish_by=_opt_date(stored.get("must_finish_by")),
        deadline_mode=_mode(stored.get("deadline_mode", "hard")),
        deadline_penalty_per_day=_penalty(stored.get("deadline_penalty_per_day"), 500.0),
        working_days_per_week=per_week,
        capacity_mode=_mode(stored.get("capacity_mode", "soft")),
        capacity_penalty=_penalty(stored.get("capacity_penalty"), 100.0),
        skill_mode=_mode(stored.get("skill_mode", "hard")),
        allow_contractors=bool(stored.get("allow_contractors", True)),
        contractor_rate_cents=_opt_int(stored.get("contractor_rate_cents")),
    )
    tasks = tuple(_parse_task(entry) for entry in data["tasks"])
    if any(task.duration_days <= 0 for task in tasks):
        raise SnapshotError("Every task needs a positive duration")
    employees = tuple(
        Employee(
            id=str(entry["id"]),
            name=str(entry["name"]),
            weekly_capacity_hours=float(entry["weekly_capacity_hours"]),
            hourly_rate_cents=_opt_int(entry.get("hourly_rate_cents")),
            is_contractor=bool(entry.get("is_contractor", False)),
            skills={
                str(skill_id): int(level) for skill_id, level in (entry.get("skills") or {}).items()
            },
        )
        for entry in data["employees"]
    )
    dependencies = tuple(
        Dependency(
            predecessor_id=str(entry["predecessor_id"]),
            successor_id=str(entry["successor_id"]),
            type=cast(DependencyType, entry.get("type", "FS")),
            lag_days=int(entry.get("lag_days", 0)),
        )
        for entry in data.get("dependencies", ())
    )
    if any(link.type not in ("FS", "SS", "FF", "SF") for link in dependencies):
        raise SnapshotError("Invalid dependency type")
    milestones = tuple(
        Milestone(
            id=str(entry["id"]),
            name=str(entry["name"]),
            due_date=date.fromisoformat(entry["due_date"]),
            mode=_mode(entry.get("mode", "hard")),
            task_ids=tuple(str(task_id) for task_id in entry.get("task_ids", ())),
        )
        for entry in data.get("milestones", ())
    )
    allocations = tuple(
        Allocation(
            employee_id=str(entry["employee_id"]),
            week_start=date.fromisoformat(entry["week_start"]),
            hours=float(entry["hours"]),
        )
        for entry in data.get("allocations", ())
    )
    return PlanningInput(
        project_id=str(data["project_id"]),
        project_name=str(data.get("project_name", "")),
        start_date=date.fromisoformat(data["start_date"]),
        tasks=tasks,
        employees=employees,
        dependencies=dependencies,
        constraints=constraints,
        milestones=milestones,
        allocations=allocations,
        deliverables={
            str(code): str(name) for code, name in (data.get("deliverables") or {}).items()
        },
        today=_opt_date(data.get("today")),
    )


# --------------------------------------------------------------------------- resources
@dataclass(frozen=True, slots=True)
class Resource:
    """Something a task can be assigned to: an employee or a generic contractor profile.

    A contractor resource stands for "as many specialists of this profile as
    needed", so it has no capacity limit.
    """

    id: str
    name: str
    kind: ResourceKind
    weekly_capacity_hours: float
    rate_cents: int
    skills: Mapping[str, int]
    employee_id: str | None = None

    def proficiency(self, skill_id: str) -> int:
        return self.skills.get(skill_id, 0)

    def skill_gap(self, task: Task) -> int:
        """Total proficiency levels missing to do `task` (0 = fully qualified)."""
        return sum(max(0, r.min_proficiency - self.proficiency(r.skill_id)) for r in task.skills)


def qualified(resource: Resource, task: Task) -> bool:
    return resource.skill_gap(task) == 0


def planned_rate_cents(employee: Employee) -> int:
    """The hourly rate the optimizer plans this person at (their own, else the default)."""
    if employee.hourly_rate_cents is not None:
        return employee.hourly_rate_cents
    return DEFAULT_HOURLY_RATE_CENTS


def build_resources(inp: PlanningInput) -> tuple[Resource, ...]:
    """Employees, plus one contractor profile per group of tasks nobody can staff.

    Contractors are added only when `allow_contractors` is on, a contractor
    rate is set, and some task has no fully qualified employee. Tasks are
    grouped by the set of skills they need; each group gets one profile with
    proficiency 5 in those skills. People without a rate are planned at
    `DEFAULT_HOURLY_RATE_CENTS`.
    """
    resources = [
        Resource(
            id=employee.id,
            name=employee.name,
            kind="employee",
            weekly_capacity_hours=employee.weekly_capacity_hours,
            rate_cents=planned_rate_cents(employee),
            skills=dict(employee.skills),
            employee_id=employee.id,
        )
        for employee in inp.employees
    ]
    constraints = inp.constraints
    if not constraints.allow_contractors or constraints.contractor_rate_cents is None:
        return tuple(resources)

    groups: dict[tuple[str, ...], dict[str, str]] = {}
    for task in inp.tasks:
        if not task.skills or any(qualified(resource, task) for resource in resources):
            continue
        key = tuple(sorted(requirement.skill_id for requirement in task.skills))
        names = groups.setdefault(key, {})
        for requirement in task.skills:
            names[requirement.skill_id] = requirement.skill_name
    for key, names in groups.items():
        label = " / ".join(names[skill_id] for skill_id in key)
        resources.append(
            Resource(
                id="contractor:" + "+".join(key),
                name=f"Contractor: {label} specialist",
                kind="contractor",
                weekly_capacity_hours=CONTRACTOR_WEEKLY_HOURS,
                rate_cents=constraints.contractor_rate_cents,
                skills=dict.fromkeys(key, MAX_PROFICIENCY),
            )
        )
    return tuple(resources)


# --------------------------------------------------------------------------- output
@dataclass(frozen=True, slots=True)
class ScheduledTask:
    task_id: str
    start: int  # working-day index from the project start (inclusive)
    end: int  # exclusive
    resource_id: str | None  # None = unassigned (baseline only)


@dataclass(frozen=True, slots=True)
class Conflict:
    """One hard constraint in an infeasible core, in plain English."""

    name: str
    explanation: str


@dataclass(frozen=True, slots=True)
class Plan:
    tasks: Mapping[str, ScheduledTask]

    @property
    def makespan(self) -> int:
        return max((s.end for s in self.tasks.values()), default=0)


@dataclass(frozen=True, slots=True)
class SolveOutcome:
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
        if self.objective is None or self.best_bound is None:
            return None
        if self.objective == 0:
            return 0.0
        return max(0.0, abs(self.objective - self.best_bound) / abs(self.objective))
