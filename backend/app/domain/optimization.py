"""Optimization run schemas: the stored result, the run request and the inputs sheet.

`RunResult` mirrors `optimization_runs.result` as written by
`app/optimization/explain.py` (plus the priced remedies when the run is
infeasible or finishes after its target date). `RunCreate` is the
optional body of `POST …/optimization/runs`; it is stored as the run's
`options`. `OptimizationInputs` is the
confirmation sheet ("Check the inputs the solver will use").
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from app.domain.changes import MAX_NEW_MEMBERS, AddedMember
from app.domain.common import CamelModel, InputModel, SolverStatus

RemedyId = Literal["specialist", "overtime", "move_deadline"]
InputSource = Literal["stated", "confirmed", "needs_you", "default", "assumed", "yours"]


class RunKpi(CamelModel):
    label: str
    value: str
    # `sub?: string` on the client: omitted rather than null when absent.
    sub: str | None = Field(default=None, exclude_if=lambda v: v is None)


class Reassignment(CamelModel):
    task: str
    current: str
    recommended: str
    reason: str


class ScheduleChange(CamelModel):
    wbs: str
    name: str
    original: str
    optimized: str
    delta: str
    reason: str


class Utilization(CamelModel):
    name: str
    before: int
    after: int


class ScheduleItem(CamelModel):
    """One work package on the timeline (dates inclusive)."""

    wbs_node_id: str
    code: str
    name: str
    deliverable_code: str
    deliverable_name: str
    start: date
    end: date
    baseline_start: date | None = None
    baseline_end: date | None = None
    assignee: str | None = None
    # Finished work (0 working days left): not scheduled again, at its applied dates.
    done: bool = False


class Specialist(CamelModel):
    """An outside specialist profile the plan uses, with its hours and cost."""

    name: str
    hours: float
    rate_cents: int
    cost_cents: int


class PeakUtilization(CamelModel):
    """The busiest team member in the plan."""

    name: str
    percent: int


class RunSummary(CamelModel):
    """Plain numbers the results headline and advice are built from."""

    start_date: date
    completion_date: date
    deadline: date | None
    deadline_mode: str
    days_late: int
    # The date this plan tries to keep (the plan in force's completion for a re-plan,
    # else the deadline) and the working days it finishes after it (0 = on time).
    target_date: date | None = None
    slip_days: int = 0
    cost_cents: int
    budget_cents: int | None
    peak_utilization: PeakUtilization | None
    specialists: list[Specialist]
    soft_violations: list[str]


class Remedy(CamelModel):
    """A priced way to keep the target date, or out of an infeasible run.

    `costCents` is the extra cost against this run's plan (0 for move_deadline
    when the plan slips); for an infeasible run, with no plan to compare with,
    it is the whole relaxed plan's cost.
    """

    id: RemedyId
    title: str
    detail: str
    cost_cents: int | None
    completion_date: date | None


class RunResult(CamelModel):
    kpis: list[RunKpi] = Field(default_factory=list)
    reassignments: list[Reassignment] = Field(default_factory=list)
    schedule_changes: list[ScheduleChange] = Field(default_factory=list)
    utilization: list[Utilization] = Field(default_factory=list)
    schedule: list[ScheduleItem] = Field(default_factory=list)
    summary: RunSummary | None = None
    remedies: list[Remedy] = Field(default_factory=list)


class Conflict(CamelModel):
    name: str
    explanation: str


class OptimizationRun(CamelModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: SolverStatus
    solver: str | None
    solve_seconds: float | None
    error: str | None
    iis: list[Conflict] | None
    result: RunResult | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # Plan vN once the run is applied; options = how the run was set up.
    version: int | None = None
    applied_at: datetime | None = None
    options: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- requests
# Working days left; 0 = done (the work package isn't scheduled again).
Days = Annotated[int, Field(ge=0, le=3650)]
Hours = Annotated[float, Field(ge=0, le=80)]


class RunOverrides(InputModel):
    """What-if changes for one run; the project's saved inputs stay as they are."""

    budget_total_cents: int | None = Field(default=None, ge=0, le=10**13)
    must_finish_by: date | None = None
    duration_days: dict[uuid.UUID, Days] | None = Field(default=None, max_length=2000)
    hours_per_week: dict[uuid.UUID, Hours] | None = Field(default=None, max_length=500)
    # People joining (with the employee record step 7 reserved for them) or leaving.
    add_members: list[AddedMember] | None = Field(default=None, max_length=MAX_NEW_MEMBERS)
    remove_members: list[uuid.UUID] | None = Field(default=None, max_length=500)


class RunCreate(InputModel):
    """Body of `POST …/optimization/runs` (every field optional), stored as `options`."""

    remedy: RemedyId | None = None
    overrides: RunOverrides | None = None
    change_request_id: uuid.UUID | None = None
    baseline_run_id: uuid.UUID | None = None


# --------------------------------------------------------------------------- inputs sheet
class InputRow(CamelModel):
    """One line of the confirmation sheet; `from` says where the value came from."""

    key: str
    label: str
    value: str
    source: InputSource
    from_: str = Field(alias="from")


class OptimizationInputs(CamelModel):
    """The confirmation sheet: every input the solver will use and where it came from."""

    rows: list[InputRow]
    ready: bool
    blocking: list[str]
    missing: list[str]
    hash: str
