"""What-if changes applied to a frozen planning input for one run.

`POST …/optimization/runs` may carry `overrides` (camelCase, exactly as
stored in `optimization_runs.options`): `budgetTotalCents`, `mustFinishBy`,
`deadlineMode` and `budgetMode` ('hard' | 'soft': "must hold" or "can
flex", e.g. "must hold" next to a date in step-7 notes), `durationDays`
(WBS node id -> working days left; 0 = done, not scheduled again),
`hoursPerWeek` (employee id -> hours), `addMembers` (people joining: `employeeId`, `fullName`,
`hoursPerWeek`, `skills` by name, `hourlyRateCents`, `workerType`; the older
`isContractor` flag is still read when `workerType` is absent) and
`removeMembers` (employee ids leaving). The API applies them to the loaded
input before freezing the snapshot, so the run is reproducible and the
project's saved inputs stay untouched (Changes & Versions writes them back
only when a change is approved). Ids that aren't in the input are ignored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from typing import Any

from app.optimization.model import ConstraintMode, Employee, PlanningInput, Task

# A newcomer's skills count at the level the Team screen gives anyone added there
# (app.repositories.team_skills.DEFAULT_PROFICIENCY).
NEW_MEMBER_PROFICIENCY = 3


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _mode(value: Any) -> ConstraintMode:
    """'hard' or 'soft' (the API validates it; anything else reads as hard)."""
    return "soft" if value == "soft" else "hard"


def _by_id(values: Mapping[Any, Any] | None) -> dict[str, Any]:
    """Keys as strings: the wire sends UUIDs, the planning input uses str ids."""
    return {str(key): value for key, value in (values or {}).items()}


def _with_duration(task: Task, days: int) -> Task:
    """The task with `days` working days left; 0 means it is done (D4)."""
    if days == 0:
        return replace(task, done=True)
    # An explicit effort scales with the duration (same people, same pace);
    # without one, effort already follows the duration at 8 h per day.
    effort = task.effort_hours
    if effort is not None and task.duration_days > 0:
        effort = round(effort * days / task.duration_days, 1)
    return replace(task, duration_days=days, effort_hours=effort)


def _is_contractor(entry: Mapping[str, Any]) -> bool:
    """`workerType == "contractor"`; entries stored before the rename carry `isContractor`."""
    worker_type = entry.get("workerType")
    if worker_type is not None:
        return str(worker_type) == "contractor"
    return bool(entry.get("isContractor", False))


def _newcomer(entry: Mapping[str, Any], skill_ids: Mapping[str, str]) -> Employee:
    """A person joining the project as a resource. Skills are matched to the work
    packages' required skills by name; a missing rate stays null, so the optimizer prices
    them by `resources.planned_rate_cents` (the standard rate for an employee)."""
    rate = entry.get("hourlyRateCents")
    return Employee(
        id=str(entry["employeeId"]),
        name=str(entry["fullName"]),
        weekly_capacity_hours=float(entry["hoursPerWeek"]),
        hourly_rate_cents=None if rate is None else int(rate),
        is_contractor=_is_contractor(entry),
        skills={
            skill_ids[name.casefold()]: NEW_MEMBER_PROFICIENCY
            for name in entry.get("skills") or []
            if name.casefold() in skill_ids
        },
    )


def _team(
    planning_input: PlanningInput, overrides: Mapping[str, Any]
) -> tuple[tuple[Employee, ...], set[str]]:
    """The team after the run's hours, removals and additions; and who was removed."""
    hours = _by_id(overrides.get("hoursPerWeek"))
    removed = {str(employee_id) for employee_id in overrides.get("removeMembers") or []}
    team = [
        replace(employee, weekly_capacity_hours=float(hours[employee.id]))
        if hours.get(employee.id) is not None
        else employee
        for employee in planning_input.employees
        if employee.id not in removed
    ]
    skill_ids = {
        requirement.skill_name.casefold(): requirement.skill_id
        for task in planning_input.tasks
        for requirement in task.skills
    }
    present = {employee.id for employee in team}
    for entry in overrides.get("addMembers") or []:
        newcomer = _newcomer(entry, skill_ids)
        if newcomer.id not in present:
            present.add(newcomer.id)
            team.append(newcomer)
    return tuple(team), removed


def apply_overrides(
    planning_input: PlanningInput, overrides: Mapping[str, Any] | None
) -> PlanningInput:
    """Return a copy of `planning_input` with the run's what-if overrides applied.

    People who leave drop out of the resources, their other-project bookings with them,
    and their work packages lose their current owner, so the solver reassigns that work
    freely instead of charging for moving it off someone who is gone.
    """
    if not overrides:
        return planning_input
    constraints = planning_input.constraints
    if overrides.get("budgetTotalCents") is not None:
        constraints = replace(constraints, budget_total_cents=int(overrides["budgetTotalCents"]))
    if overrides.get("mustFinishBy") is not None:
        constraints = replace(constraints, must_finish_by=_as_date(overrides["mustFinishBy"]))
    if overrides.get("deadlineMode") is not None:
        constraints = replace(constraints, deadline_mode=_mode(overrides["deadlineMode"]))
    if overrides.get("budgetMode") is not None:
        constraints = replace(constraints, budget_mode=_mode(overrides["budgetMode"]))
    durations = _by_id(overrides.get("durationDays"))
    employees, removed = _team(planning_input, overrides)
    # A missing duration keeps the WBS value; 0 days left marks the work done.
    tasks = tuple(
        _with_duration(task, int(durations[task.id]))
        if durations.get(task.id) is not None
        else task
        for task in planning_input.tasks
    )
    tasks = tuple(
        replace(task, owner_id=None) if task.owner_id in removed else task for task in tasks
    )
    allocations = tuple(
        booking for booking in planning_input.allocations if booking.employee_id not in removed
    )
    return replace(
        planning_input,
        constraints=constraints,
        tasks=tasks,
        employees=employees,
        allocations=allocations,
    )


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


def with_plan_in_force(
    inp: PlanningInput, dates: Mapping[str, tuple[date, date]], completion: date | None
) -> PlanningInput:
    """Remember the plan in force: each work package's dates (WBS node id -> first and
    last day), so finished work stays where it happened, and its completion date,
    the target a re-plan tries to keep (round-2 D2)."""
    tasks = tuple(
        replace(task, applied_start=dates[task.id][0], applied_end=dates[task.id][1])
        if task.id in dates
        else task
        for task in inp.tasks
    )
    return replace(inp, tasks=tasks, target_date=completion or inp.target_date)
