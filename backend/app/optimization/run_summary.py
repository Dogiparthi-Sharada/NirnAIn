"""Timeline rows and the plain-number summary of a solved plan.

Purpose: The Optimization Results screen and the dashboard build their headline and advice from
these facts read off the plan (completion, cost, deadline, target and slip, peak utilization,
specialists and the soft constraints it bends); nothing is estimated or invented.
How it fits: `explain.build_report` adds them to `optimization_runs.result` as `schedule` and
`summary`; layer: optimization.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from app.optimization import formatting as fmt
from app.optimization.calendar import workdays_late, workdays_through
from app.optimization.costing import spend_limit_cents
from app.optimization.model import Task, target_of
from app.optimization.outcome import qualified

if TYPE_CHECKING:
    from app.optimization.report_context import ReportContext


def deliverable_code(code: str) -> str:
    """`1.2` for work package `1.2.3`; a level-2 work package is its own deliverable."""
    parts = code.split(".")
    return ".".join(parts[:2]) if len(parts) >= 3 else code


def timeline(context: ReportContext, ordered: list[Task]) -> list[dict[str, Any]]:
    """Every work package with its planned and baseline dates (both inclusive)."""
    rows = []
    for task in ordered:
        planned, baseline = context.plan.tasks[task.id], context.baseline.tasks[task.id]
        group = deliverable_code(task.code)
        rows.append(
            {
                "wbsNodeId": task.id,
                "code": task.code,
                "name": task.name,
                "deliverableCode": group,
                "deliverableName": context.planning_input.deliverables.get(group)
                or (task.name if group == task.code else group),
                "start": context.date_of(planned.start).isoformat(),
                # Working-day ends are exclusive; the screen shows the last day worked.
                "end": context.date_of(max(planned.end - 1, planned.start)).isoformat(),
                "baselineStart": context.date_of(baseline.start).isoformat(),
                "baselineEnd": context.date_of(max(baseline.end - 1, baseline.start)).isoformat(),
                "assignee": context.name(planned.resource_id) if planned.resource_id else None,
                # Finished work, shown at the dates of the plan in force.
                "done": task.done,
            }
        )
    return rows


def soft_violations(context: ReportContext, cost: int, finish: date) -> list[str]:
    """Soft constraints this plan bends, in plain English (hard ones always hold)."""
    constraints, plan = context.planning_input.constraints, context.plan
    violations = []
    if constraints.must_finish_by is not None and finish > constraints.must_finish_by:
        late_days = (finish - constraints.must_finish_by).days
        violations.append(
            f"Go-live {fmt.plural(late_days, 'day')} after {fmt.day(constraints.must_finish_by)}"
        )
    for milestone in context.planning_input.milestones:
        ends = [plan.tasks[task_id].end for task_id in milestone.task_ids if task_id in plan.tasks]
        if not ends:
            continue
        due_index = workdays_through(
            context.planning_input.start_date, milestone.due_date, context.per_week
        )
        if max(ends) > due_index:
            lands = context.date_of(max(ends) - 1)
            late_days = (lands - milestone.due_date).days
            violations.append(
                f"{milestone.name} lands {fmt.day(lands)} ({fmt.plural(late_days, 'day')} late)"
            )
    for label, cap in (
        ("budget", constraints.budget_total_cents),
        ("labor cap", constraints.labor_cap_cents),
    ):
        if cap is not None and cost > cap:
            violations.append(f"{fmt.money(cost - cap)} over the {label}")
    # A soft budget may also eat into the reserve held back from it (R5).
    budget, limit = constraints.budget_total_cents, spend_limit_cents(constraints)
    if budget is not None and limit is not None and limit < cost <= budget:
        violations.append(
            f"{fmt.money(cost - limit)} of the {fmt.money(budget - limit)} reserve used"
        )
    overbooked = [
        resource.name
        for resource in context.resources.values()
        if resource.kind == "employee" and context.over_allocated(plan, resource.id)
    ]
    if overbooked:
        violations.append(f"{', '.join(overbooked)} scheduled above their hours")
    for scheduled in plan.tasks.values():
        resource = context.resources.get(scheduled.resource_id) if scheduled.resource_id else None
        task = context.tasks[scheduled.task_id]
        if resource is not None and not qualified(resource, task):
            violations.append(f"{task.label} given to {resource.name} without the required skill")
    return violations


def peak_utilization(context: ReportContext) -> dict[str, Any] | None:
    """The busiest working day of anyone with work left in the plan: their load that day,
    other projects' bookings included, as a percent of their daily hours, and who it is.

    A real peak (QA pack RES-021), not an average over the plan: someone who works
    8 h on one day of a 40 h week peaks at 100%. Ties go to the first person listed.
    """
    working = {
        scheduled.resource_id
        for scheduled in context.plan.tasks.values()
        if not context.tasks[scheduled.task_id].done
    }
    peaks = [
        (resource.name, context.peak_percent(context.plan, resource))
        for resource in context.resources.values()
        if resource.kind == "employee" and resource.id in working
    ]
    busiest = max(peaks, key=lambda peak: peak[1], default=None)
    return None if busiest is None else {"name": busiest[0], "percent": busiest[1]}


def run_summary(context: ReportContext, cost: int, finish: date) -> dict[str, Any]:
    """Plain numbers the results screen builds its headline and advice from."""
    constraints = context.planning_input.constraints
    specialists = []
    for resource in context.resources.values():
        if resource.kind != "contractor":
            continue
        hours = context.hours(context.plan, resource.id)
        if hours > 0:
            specialists.append(
                {
                    "name": resource.name,
                    "hours": hours,
                    "rateCents": resource.rate_cents,
                    "costCents": round(hours * resource.rate_cents),
                }
            )
    budget = (
        constraints.budget_total_cents
        if constraints.budget_total_cents is not None
        else constraints.labor_cap_cents
    )
    deadline = constraints.must_finish_by
    target = target_of(context.planning_input)
    return {
        "startDate": context.date_of(0).isoformat(),
        "completionDate": finish.isoformat(),
        "deadline": deadline.isoformat() if deadline else None,
        "deadlineMode": constraints.deadline_mode,
        "daysLate": max(0, (finish - deadline).days) if deadline is not None else 0,
        # The date to keep (the plan in force's completion for a re-plan, else the
        # deadline) and how many working days this plan finishes after it (D2).
        "targetDate": target.isoformat() if target else None,
        "slipDays": (
            workdays_late(context.planning_input.start_date, target, finish, context.per_week)
            if target
            else 0
        ),
        "costCents": cost,
        "budgetCents": budget,
        "peakUtilization": peak_utilization(context),
        "specialists": specialists,
        "softViolations": soft_violations(context, cost, finish),
    }
