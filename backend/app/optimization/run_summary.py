"""Timeline rows and plain-number summary of a solved plan.

`explain.build_report` adds these to `optimization_runs.result` as
`schedule` (one row per work package, for the Gantt grouped by deliverable)
and `summary` (completion, cost, deadline, specialists and the soft
constraints the plan bends). The Optimization Results screen builds its
headline and advice from them, so everything here is a fact read off the
plan: nothing is estimated or invented.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from app.optimization import formatting as fmt
from app.optimization.calendar import workdays_through
from app.optimization.model import Task, qualified

if TYPE_CHECKING:
    from app.optimization.report_context import ReportContext


def deliverable_code(code: str) -> str:
    """`1.2` for work package `1.2.3`; a level-2 work package is its own deliverable."""
    parts = code.split(".")
    return ".".join(parts[:2]) if len(parts) >= 3 else code


def timeline(ctx: ReportContext, ordered: list[Task]) -> list[dict[str, Any]]:
    """Every work package with its planned and baseline dates (both inclusive)."""
    rows = []
    for task in ordered:
        planned, baseline = ctx.plan.tasks[task.id], ctx.baseline.tasks[task.id]
        group = deliverable_code(task.code)
        rows.append(
            {
                "wbsNodeId": task.id,
                "code": task.code,
                "name": task.name,
                "deliverableCode": group,
                "deliverableName": ctx.inp.deliverables.get(group)
                or (task.name if group == task.code else group),
                "start": ctx.date_of(planned.start).isoformat(),
                # Working-day ends are exclusive; the screen shows the last day worked.
                "end": ctx.date_of(max(planned.end - 1, planned.start)).isoformat(),
                "baselineStart": ctx.date_of(baseline.start).isoformat(),
                "baselineEnd": ctx.date_of(max(baseline.end - 1, baseline.start)).isoformat(),
                "assignee": ctx.name(planned.resource_id) if planned.resource_id else None,
                # Finished work, shown at the dates of the plan in force.
                "done": task.done,
            }
        )
    return rows


def soft_violations(ctx: ReportContext, cost: int, finish: date) -> list[str]:
    """Soft constraints this plan bends, in plain English (hard ones always hold)."""
    constraints, plan = ctx.inp.constraints, ctx.plan
    violations = []
    if constraints.must_finish_by is not None and finish > constraints.must_finish_by:
        late_days = (finish - constraints.must_finish_by).days
        violations.append(
            f"Go-live {fmt.plural(late_days, 'day')} after {fmt.day(constraints.must_finish_by)}"
        )
    for milestone in ctx.inp.milestones:
        ends = [plan.tasks[task_id].end for task_id in milestone.task_ids if task_id in plan.tasks]
        if not ends:
            continue
        due_index = workdays_through(ctx.inp.start_date, milestone.due_date, ctx.per_week)
        if max(ends) > due_index:
            lands = ctx.date_of(max(ends) - 1)
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
    overbooked = [
        resource.name
        for resource in ctx.resources.values()
        if resource.kind == "employee" and ctx.over_allocated(plan, resource.id)
    ]
    if overbooked:
        violations.append(f"{', '.join(overbooked)} scheduled above their hours")
    for scheduled in plan.tasks.values():
        resource = ctx.resources.get(scheduled.resource_id) if scheduled.resource_id else None
        task = ctx.tasks[scheduled.task_id]
        if resource is not None and not qualified(resource, task):
            violations.append(f"{task.label} given to {resource.name} without the required skill")
    return violations


def run_summary(
    ctx: ReportContext, cost: int, finish: date, utilization: list[dict[str, Any]]
) -> dict[str, Any]:
    """Plain numbers the results screen builds its headline and advice from."""
    constraints = ctx.inp.constraints
    specialists = []
    for resource in ctx.resources.values():
        if resource.kind != "contractor":
            continue
        hours = ctx.hours(ctx.plan, resource.id)
        if hours > 0:
            specialists.append(
                {
                    "name": resource.name,
                    "hours": hours,
                    "rateCents": resource.rate_cents,
                    "costCents": round(hours * resource.rate_cents),
                }
            )
    employee_names = {
        resource.name for resource in ctx.resources.values() if resource.kind == "employee"
    }
    staff = [row for row in utilization if row["name"] in employee_names]
    peak = max(staff, key=lambda row: row["after"], default=None)
    budget = (
        constraints.budget_total_cents
        if constraints.budget_total_cents is not None
        else constraints.labor_cap_cents
    )
    deadline = constraints.must_finish_by
    return {
        "startDate": ctx.date_of(0).isoformat(),
        "completionDate": finish.isoformat(),
        "deadline": deadline.isoformat() if deadline else None,
        "deadlineMode": constraints.deadline_mode,
        "daysLate": max(0, (finish - deadline).days) if deadline is not None else 0,
        "costCents": cost,
        "budgetCents": budget,
        "peakUtilization": (
            {"name": peak["name"], "percent": peak["after"]} if peak is not None else None
        ),
        "specialists": specialists,
        "softViolations": soft_violations(ctx, cost, finish),
    }
