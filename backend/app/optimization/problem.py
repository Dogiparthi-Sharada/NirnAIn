"""Pre-computed numbers behind the CP-SAT model and its explanations.

`prepare` turns a frozen planning input into what the model needs as plain
integers: who may take each task, how long it takes with each of them and
the load it puts on them per day (`load.task_mode`: their skill level sets
their pace, `speed.py`, and part-timers stretch tasks; `load.booked_modes`:
it may also stretch to the hours other projects' bookings leave), the labor
cost of each choice, daily capacities, other projects' bookings, the
horizon and the deadline / milestone indices. Only unfinished
work is modelled, from `release` (today) on; finished work is fixed at the
dates of the plan in force (`baseline.done_span`). The main model
and the explanation model (`builder.py`) and the conflict texts
(`conflicts.py`) all read the same `Problem`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.optimization.baseline import baseline_plan, planning_start, task_dependencies
from app.optimization.calendar import workday_date, workdays_through
from app.optimization.load import (
    Block,
    Mode,
    allocation_blocks,
    booked_modes,
    daily_capacity_tenths,
    flex_capacity_tenths,
    task_mode,
)
from app.optimization.model import (
    Dependency,
    Plan,
    PlanningInput,
    Resource,
    ScheduledTask,
    Task,
    qualified,
    target_of,
)
from app.optimization.resources import build_resources, candidates_for
from app.optimization.speed import pace_percent, work_hours


@dataclass(slots=True)
class Problem:
    """Pre-computed numbers shared by the main and the explanation model."""

    inp: PlanningInput
    resources: tuple[Resource, ...]
    baseline: Plan
    tasks: dict[str, Task]  # unfinished work: what the model schedules
    done: dict[str, ScheduledTask]  # finished work, fixed where it happened
    dependencies: list[Dependency]
    release: int  # first working day unfinished work may use (today)
    horizon: int
    capacity: dict[str, int]  # employee id -> tenths of an hour per working day
    blocks: dict[str, list[Block]]
    candidates: dict[str, list[Resource]]  # everyone who may ever take the task
    # (task id, resource id) -> percent of the WBS effort at the person's level (D5)
    pace: dict[tuple[str, str], int]
    modes: dict[tuple[str, str], Mode]  # (task id, resource id) -> duration and daily load
    # The same at up to 25% over the person's hours, for "Can flex" (employees only).
    flex_modes: dict[tuple[str, str], Mode]
    # Longer ones at the hours their bookings on other projects leave (RES-006).
    booked: dict[tuple[str, str], list[Mode]]
    cost: dict[tuple[str, str], int]  # (task id, resource id) -> cents
    deadline: int | None  # max exclusive end index
    target: int | None  # exclusive end index of the date to keep (`model.target_of`)
    milestone_ends: dict[str, int] = field(default_factory=dict)

    def finish_date(self, end: int) -> date:
        """Calendar date of the last working day before the exclusive end index `end`."""
        return workday_date(
            self.inp.start_date, max(end - 1, 0), self.inp.constraints.working_days_per_week
        )

    def allowed(self, task: Task) -> list[Resource]:
        """Candidates the main model may assign (only qualified ones when skills must hold)."""
        if self.inp.constraints.skill_mode == "soft":
            return self.candidates[task.id]
        return [resource for resource in self.candidates[task.id] if qualified(resource, task)]


def prepare(inp: PlanningInput) -> Problem:
    """Everything the model needs from `inp`, as integers in working days and cents."""
    constraints = inp.constraints
    per_week = constraints.working_days_per_week
    resources = build_resources(inp)
    blocks = allocation_blocks(inp)
    links = task_dependencies(inp)
    baseline = baseline_plan(inp)
    release = planning_start(inp)
    work = [task for task in inp.tasks if not task.done]
    target = target_of(inp)
    capacity = {
        resource.id: daily_capacity_tenths(resource.weekly_capacity_hours, per_week)
        for resource in resources
        if resource.kind == "employee"
    }
    candidates = candidates_for(work, resources)
    pace = {
        (task.id, resource.id): pace_percent(resource, task)
        for task in work
        for resource in candidates[task.id]
    }
    # Contractor profiles have no capacity entry, so they keep the WBS duration.
    modes = {
        (task.id, resource.id): task_mode(
            task, capacity.get(resource.id), pace[task.id, resource.id]
        )
        for task in work
        for resource in candidates[task.id]
    }
    flex_modes = {
        (task.id, resource.id): task_mode(
            task, flex_capacity_tenths(capacity[resource.id]), pace[task.id, resource.id]
        )
        for task in work
        for resource in candidates[task.id]
        if resource.id in capacity
    }
    booked = {
        (task.id, resource.id): booked_modes(
            task, capacity[resource.id], blocks[resource.id], pace[task.id, resource.id]
        )
        for task in work
        for resource in candidates[task.id]
        if resource.id in blocks
    }
    # With each task at its longest candidate duration a serial schedule always fits.
    longest = sum(
        max(
            (modes[task.id, resource.id].duration_days for resource in candidates[task.id]),
            default=task.duration_days,
        )
        for task in work
    )
    tasks = {task.id: task for task in work}
    return Problem(
        inp=inp,
        resources=resources,
        baseline=baseline,
        tasks=tasks,
        done={task.id: baseline.tasks[task.id] for task in inp.tasks if task.done},
        dependencies=links,
        release=release,
        horizon=(
            release
            + longest
            + sum(abs(link.lag_days) for link in links)
            + max((block.hi for booked in blocks.values() for block in booked), default=0)
            + per_week
        ),
        capacity=capacity,
        blocks=blocks,
        candidates=candidates,
        pace=pace,
        modes=modes,
        flex_modes=flex_modes,
        booked=booked,
        cost={
            (task.id, resource.id): round(work_hours(task, resource) * resource.rate_cents)
            for task in work
            for resource in candidates[task.id]
        },
        deadline=(
            workdays_through(inp.start_date, constraints.must_finish_by, per_week)
            if constraints.must_finish_by is not None
            else None
        ),
        target=None if target is None else workdays_through(inp.start_date, target, per_week),
        milestone_ends={
            milestone.id: workdays_through(inp.start_date, milestone.due_date, per_week)
            for milestone in inp.milestones
            if any(task_id in tasks for task_id in milestone.task_ids)
        },
    )
