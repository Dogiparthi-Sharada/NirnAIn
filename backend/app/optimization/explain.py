"""Turn a solve outcome into what the API stores and the UI shows (plan.md §6.4).

Purpose: Every change the optimizer recommends needs a reason the PM can check. Reasons are
rule-based, from the diff between the baseline and the optimized plan; nothing is invented:
    moved later and a predecessor moved too    -> "Dependency cascade from {pred}"
    moved later behind the same assignee       -> "Waits for {name} to finish {task}"
    moved later, person busy on other projects -> "{name} is committed to other projects"
    same start, other duration for the person  -> "{name} (level 5) at {h}h/week: {n} working
                                                  days instead of {wbs}" (pace and hours, D5)
    taken off a person over 100% in baseline   -> "Over-allocation"
    given to someone with the required skill   -> "Skill gap"
    unchanged                                  -> omitted from reassignments, "—" delta
    finished (0 days left)                     -> "Done", at the dates of the plan in force
How it fits: `build_report` is called by the optimizer job (app/jobs/optimizer.py); lookups live
in report_context.py, the timeline and summary in run_summary.py; layer: optimization.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.optimization import formatting as fmt
from app.optimization.load import paced_days
from app.optimization.model import PlanningInput, Task
from app.optimization.outcome import RunStatus, SolveOutcome, qualified
from app.optimization.report_context import ReportContext, critical_chain, planned_days
from app.optimization.resources import CONTRACTOR_ID
from app.optimization.run_summary import run_summary, timeline
from app.optimization.speed import pace_level, pace_percent, work_hours


@dataclass(frozen=True, slots=True)
class AssignmentRow:
    """One `planned_assignments` row (weeks are 0-based calendar weeks)."""

    wbs_node_id: str
    employee_id: str | None
    contractor_label: str | None
    start_week: int
    end_week: int
    hours_per_week: float
    baseline_start_week: int
    baseline_end_week: int
    baseline_employee_id: str | None


@dataclass(frozen=True, slots=True)
class RecommendationRow:
    """One stored recommendation (`recommendations` row): what changes and why."""

    type: str  # recommendation_type: reassignment | schedule_change | hire | note
    wbs_node_id: str | None
    before_value: str | None
    after_value: str | None
    reason: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class Report:
    """Everything stored for a finished run: status, KPIs, result payload and the rows to insert."""

    status: RunStatus
    solver: str
    solver_version: str
    runtime_ms: int
    objective_value: float | None = None
    mip_gap: float | None = None
    expected_cost_cents: int | None = None
    completion_date: date | None = None
    avg_utilization: float | None = None
    critical_path: list[dict[str, str]] | None = None
    result: dict[str, Any] | None = None
    iis: list[dict[str, str]] | None = None
    error: str | None = None
    assignments: list[AssignmentRow] = field(default_factory=list)
    recommendations: list[RecommendationRow] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers
def _chain_label(chain: list[Task]) -> str:
    """Return the critical chain as "A → B → … → Z chain" (at most four names)."""
    names = [task.name for task in chain]
    if len(names) > 4:
        names = [*names[:2], "…", names[-1]]
    return " → ".join(names) + (" chain" if len(chain) > 1 else "")


# --------------------------------------------------------------------------- reasons
def _reassignment_reason(context: ReportContext, task: Task, before: str | None, after: str) -> str:
    """Return why a task moved to another person: skill gap, over-allocation, cost or balance."""
    reasons = []
    old = context.resources.get(before) if before else None
    new = context.resources[after]
    if task.skills and (old is None or not qualified(old, task)) and qualified(new, task):
        reasons.append("Skill gap")
    if before is not None and context.over_allocated(context.baseline, before):
        reasons.append("Over-allocation")
    if reasons:
        first, *rest = reasons
        return " + ".join([first, *(reason.lower() for reason in rest)])
    if before is None:
        return "No owner assigned"
    before_cost = round(work_hours(task, old) * old.rate_cents) if old else 0
    saved = before_cost - round(work_hours(task, new) * new.rate_cents)
    if saved > 0:
        return f"Lower cost (saves {fmt.money(saved)})"
    return "Balances workload"


def _duration_reason(context: ReportContext, task_id: str) -> str | None:
    """Why a task that starts on time takes another number of days than its WBS duration:
    the person's skill level (D5), their hours, or both."""
    scheduled, task = context.plan.tasks[task_id], context.tasks[task_id]
    days = planned_days(context.plan, task_id)
    resource = context.resources.get(scheduled.resource_id) if scheduled.resource_id else None
    if days == task.duration_days or resource is None:
        return None
    level = pace_level(resource, task)
    who = resource.name + (f" (level {level})" if level is not None else "")
    if days > paced_days(task, pace_percent(resource, task)):
        who += f" at {context.hours_label(task, resource, days)}"
    return f"{who}: {fmt.plural(days, 'working day')} instead of {task.duration_days}"


def _schedule_reason(context: ReportContext, task_id: str, shift: int) -> str:
    """Return why a task's dates changed (cascade, waiting, other projects or its duration)."""
    plan, base = context.plan, context.baseline
    scheduled = plan.tasks[task_id]
    if shift == 0:
        return _duration_reason(context, task_id) or "No change needed"
    if shift < 0:
        return "Starts earlier"
    for predecessor in context.tight_predecessors(plan, task_id):
        if plan.tasks[predecessor].start > base.tasks[predecessor].start:
            return f"Dependency cascade from {context.tasks[predecessor].name}"
    blocker = next(
        (
            other
            for other in plan.tasks.values()
            if other.task_id != task_id
            and other.resource_id == scheduled.resource_id
            and other.end == scheduled.start
        ),
        None,
    )
    if blocker is not None:
        name = context.name(scheduled.resource_id)
        return f"Waits for {name} to finish {context.tasks[blocker.task_id].name}"
    if scheduled.resource_id in context.blocks:
        return f"{context.name(scheduled.resource_id)} is committed to other projects"
    return "Resolves a resource conflict"


# --------------------------------------------------------------------------- report
def _kpis(
    context: ReportContext,
    outcome: SolveOutcome,
    cost: int,
    finish: date,
    utilization: int,
    resource_count: int,
    chain: list[Task],
) -> list[dict[str, str]]:
    """Return the KPI cards: budget, completion, utilization, critical path and solve quality."""
    constraints = context.planning_input.constraints
    budget = (
        constraints.budget_total_cents
        if constraints.budget_total_cents is not None
        else constraints.labor_cap_cents
    )
    if budget is None:
        budget_sub = "No budget set"
    elif cost <= budget:
        budget_sub = f"{fmt.money(budget - cost)} under budget"
    else:
        budget_sub = f"{fmt.money(cost - budget)} over budget"
    if constraints.must_finish_by is None:
        finish_sub = "No deadline set"
    elif finish == constraints.must_finish_by:
        finish_sub = "Meets target date"
    elif finish < constraints.must_finish_by:
        finish_sub = (
            f"{fmt.plural((constraints.must_finish_by - finish).days, 'day')} ahead of target"
        )
    else:
        finish_sub = f"{fmt.plural((finish - constraints.must_finish_by).days, 'day')} over target"
    chain_days = context.plan.tasks[chain[-1].id].end - context.plan.tasks[chain[0].id].start
    chain_weeks = max(1, math.ceil(chain_days / context.per_week))
    kpis = [
        {"label": "Budget", "value": fmt.money(cost), "sub": budget_sub},
        {"label": "Completion Date", "value": fmt.day(finish), "sub": finish_sub},
        {
            "label": "Resource Utilization",
            "value": f"{utilization}%",
            "sub": f"Average across {fmt.plural(resource_count, 'resource')}",
        },
        {
            "label": "Critical Path",
            "value": fmt.plural(chain_weeks, "week"),
            "sub": _chain_label(chain),
        },
    ]
    if outcome.hit_time_limit:
        gap = outcome.gap
        kpis.append(
            {
                "label": "Solve",
                "value": "Time limit reached",
                "sub": "Best plan found"
                + (f", within {gap:.1%} of optimal" if gap is not None else ""),
            }
        )
    return kpis


def _task_rows(
    context: ReportContext,
    task: Task,
    reassignments: list[dict[str, str]],
    recs: list[RecommendationRow],
) -> tuple[AssignmentRow, dict[str, str]]:
    """The task's `planned_assignments` row and schedule change; adds its recommendations."""
    planned, before = context.plan.tasks[task.id], context.baseline.tasks[task.id]
    resource = context.resources[planned.resource_id] if planned.resource_id else None
    start_week, end_week = context.weeks(planned.start, planned.end)
    baseline_start_week, baseline_end_week = context.weeks(before.start, before.end)
    # Effort spread over the days the plan gives it (longer at a part-timer's hours);
    # finished work has none left.
    days = planned_days(context.plan, task.id)
    hours = work_hours(task, resource) if resource else task.hours
    hours_per_week = min(round(hours / days * context.per_week, 1), 168.0) if days else 0.0
    assignment = AssignmentRow(
        wbs_node_id=task.id,
        employee_id=resource.employee_id if resource else None,
        contractor_label=resource.name if resource and resource.kind == "contractor" else None,
        start_week=start_week,
        end_week=end_week,
        hours_per_week=hours_per_week,
        baseline_start_week=baseline_start_week,
        baseline_end_week=baseline_end_week,
        baseline_employee_id=before.resource_id,
    )
    if planned.resource_id != before.resource_id and planned.resource_id is not None:
        current = context.name(before.resource_id)
        if before.resource_id is not None:
            previous = context.resources[before.resource_id]
            current += f" ({context.utilization(context.baseline, previous)}% util)"
        row = {
            "task": task.label,
            "current": current,
            "recommended": f"{context.name(planned.resource_id)} ({hours_per_week:g}h/wk)",
            "reason": _reassignment_reason(context, task, before.resource_id, planned.resource_id),
        }
        reassignments.append(row)
        recs.append(
            RecommendationRow(
                "reassignment",
                task.id,
                row["current"],
                row["recommended"],
                row["reason"],
                len(recs),
            )
        )
    shift = planned.start - before.start
    change = {
        "wbs": task.code,
        "name": task.name,
        "original": fmt.weeks_label(baseline_start_week, baseline_end_week),
        "optimized": fmt.weeks_label(start_week, end_week),
        "delta": fmt.shift(shift, context.per_week),
        "reason": "Done" if task.done else _schedule_reason(context, task.id, shift),
    }
    if shift or days != before.end - before.start:
        recs.append(
            RecommendationRow(
                "schedule_change",
                task.id,
                change["original"],
                change["optimized"],
                change["reason"],
                len(recs),
            )
        )
    return assignment, change


def build_report(planning_input: PlanningInput, outcome: SolveOutcome) -> Report:
    """Everything stored for a run: KPIs and result payload, DB rows, or the IIS."""
    runtime_ms = round(outcome.wall_seconds * 1000)
    common: dict[str, Any] = {
        "status": outcome.status,
        "solver": outcome.solver,
        "solver_version": outcome.solver_version,
        "runtime_ms": runtime_ms,
    }
    if outcome.plan is None:
        iis = (
            [
                {"name": conflict.name, "explanation": conflict.explanation}
                for conflict in outcome.conflicts
            ]
            if outcome.status == "infeasible"
            else None
        )
        return Report(**common, iis=iis, error=outcome.message)

    plan, base = outcome.plan, outcome.baseline
    context = ReportContext(planning_input, outcome, plan)
    ordered = sorted(
        planning_input.tasks,
        key=lambda task: [int(part) if part.isdigit() else 0 for part in task.code.split(".")],
    )

    assignments: list[AssignmentRow] = []
    reassignments: list[dict[str, str]] = []
    schedule: list[dict[str, str]] = []
    recs: list[RecommendationRow] = []
    for task in ordered:
        assignment, change = _task_rows(context, task, reassignments, recs)
        assignments.append(assignment)
        schedule.append(change)

    used = {scheduled.resource_id for scheduled in plan.tasks.values()}
    before_used = {scheduled.resource_id for scheduled in base.tasks.values()}
    utilization: list[dict[str, Any]] = []
    for resource in outcome.resources:
        if resource.id not in used and resource.id not in before_used:
            continue
        before = context.utilization(base, resource) if resource.kind == "employee" else 0
        utilization.append(
            {"name": resource.name, "before": before, "after": context.utilization(plan, resource)}
        )
        if resource.kind == "contractor" and resource.id in used:
            hours = context.hours(plan, resource.id)
            recs.append(
                RecommendationRow(
                    "hire",
                    None,
                    None,
                    resource.name,
                    f"{'Late work' if resource.id == CONTRACTOR_ID else 'Skill gap'}: "
                    f"about {hours:g} hours, est. "
                    f"{fmt.money(round(hours * resource.rate_cents))}",
                    len(recs),
                )
            )
    after = [row["after"] for row in utilization if row["after"] > 0]
    avg = round(sum(after) / len(after)) if after else 0

    cost = context.cost(plan)
    finish = context.date_of(max(plan.makespan - 1, 0))
    chain = critical_chain(context, plan)
    result = {
        "kpis": _kpis(context, outcome, cost, finish, avg, len(after), chain),
        "reassignments": reassignments,
        "scheduleChanges": schedule,
        "utilization": utilization,
        "schedule": timeline(context, ordered),
        "summary": run_summary(context, cost, finish),
    }
    return Report(
        **common,
        objective_value=outcome.objective,
        mip_gap=outcome.gap,
        expected_cost_cents=cost,
        completion_date=finish,
        avg_utilization=avg / 100,
        critical_path=[{"id": task.id, "code": task.code, "name": task.name} for task in chain],
        result=result,
        error=None,
        assignments=assignments,
        recommendations=recs,
    )
