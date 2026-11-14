"""What-if changes applied to a frozen planning input for one run.

`POST …/optimization/runs` may carry `overrides` (camelCase, exactly as
stored in `optimization_runs.options`): `budgetTotalCents`, `mustFinishBy`,
`durationDays` (WBS node id -> working days) and `hoursPerWeek` (employee id
-> hours). The API applies them to the loaded input before freezing the
snapshot, so the run is reproducible and the project's saved inputs stay
untouched (Changes & Versions writes them back only when a change is
approved). Ids that aren't in the input are ignored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from typing import Any

from app.optimization.model import PlanningInput, Task


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _by_id(values: Mapping[Any, Any] | None) -> dict[str, Any]:
    """Keys as strings: the wire sends UUIDs, the planning input uses str ids."""
    return {str(key): value for key, value in (values or {}).items()}


def _with_duration(task: Task, days: int) -> Task:
    # An explicit effort scales with the duration (same people, same pace);
    # without one, effort already follows the duration at 8 h per day.
    effort = task.effort_hours
    if effort is not None and task.duration_days > 0:
        effort = round(effort * days / task.duration_days, 1)
    return replace(task, duration_days=days, effort_hours=effort)


def apply_overrides(inp: PlanningInput, overrides: Mapping[str, Any] | None) -> PlanningInput:
    """Return a copy of `inp` with the run's what-if overrides applied."""
    if not overrides:
        return inp
    constraints = inp.constraints
    if overrides.get("budgetTotalCents") is not None:
        constraints = replace(constraints, budget_total_cents=int(overrides["budgetTotalCents"]))
    if overrides.get("mustFinishBy") is not None:
        constraints = replace(constraints, must_finish_by=_as_date(overrides["mustFinishBy"]))
    durations = _by_id(overrides.get("durationDays"))
    hours = _by_id(overrides.get("hoursPerWeek"))
    # A zero or missing duration keeps the WBS value: work packages always take time.
    tasks = tuple(
        _with_duration(task, int(durations[task.id]))
        if int(durations.get(task.id) or 0) > 0
        else task
        for task in inp.tasks
    )
    employees = tuple(
        replace(employee, weekly_capacity_hours=float(hours[employee.id]))
        if hours.get(employee.id) is not None
        else employee
        for employee in inp.employees
    )
    return replace(inp, constraints=constraints, tasks=tasks, employees=employees)


def with_baseline(inp: PlanningInput, owners: Mapping[str, str]) -> PlanningInput:
    """Use an earlier run's assignments (WBS node id -> employee id) as current owners.

    The solver charges for every work package moved off its current owner,
    so a re-plan against the plan in force changes as few assignments as
    possible. Owners who are no longer on the team are ignored.
    """
    team = {employee.id for employee in inp.employees}
    tasks = tuple(
        replace(task, owner_id=owners[task.id]) if owners.get(task.id) in team else task
        for task in inp.tasks
    )
    return replace(inp, tasks=tasks)
