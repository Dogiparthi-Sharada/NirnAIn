"""What a plan costs: everyone's hours at their rate, plus the overtime premium.

Purpose: Round-2 D1: employees and contractors both count toward the budget and costs come only from
hours x rate. Overtime is billed at 1.5x an employee's cost and at a contractor's own rate; every
hour is already counted once, so overtime adds `overtime rate - rate`. The contingency reserve is
held back (round 3, R5): a plan may spend the budget less the reserve.
How it fits: The report (report_context.py), the remedies and the model's budget cap price plans
with these functions, so the numbers on screen agree; layer: optimization.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Mapping

from app.optimization.load import TENTHS, Block, daily_capacity_tenths, daily_tenths
from app.optimization.model import Constraints, PlanningInput
from app.optimization.outcome import Plan, Resource
from app.optimization.speed import WBS_PACE, pace_percent, work_hours


def daily_load(
    planning_input: PlanningInput,
    plan: Plan,
    resource: Resource,
    blocks: Mapping[str, list[Block]],
    paced: bool = True,
) -> list[int]:
    """Tenths of an hour the person works on each working day of the plan.

    Bookings on other projects count, and each task's effort at the person's
    pace (`speed.py`; the WBS effort when not `paced`) is spread over the
    days the plan gives it, as in the solver. Finished work has no load left.
    """
    tasks = {task.id: task for task in planning_input.tasks}
    span = plan.makespan
    load = [0] * span
    for block in blocks.get(resource.id, []):
        for day in range(block.lo, min(block.hi, span)):
            load[day] += block.tenths
    for scheduled in plan.tasks.values():
        task = tasks[scheduled.task_id]
        if scheduled.resource_id != resource.id or task.done:
            continue
        pace = pace_percent(resource, task) if paced else WBS_PACE
        demand = daily_tenths(task, scheduled.end - scheduled.start, pace)
        for day in range(scheduled.start, scheduled.end):
            load[day] += demand
    return load


def overtime_hours(
    planning_input: PlanningInput, plan: Plan, resource: Resource, blocks: Mapping[str, list[Block]]
) -> float:
    """Hours the person works above their daily capacity over the whole plan."""
    if resource.kind != "employee":
        return 0.0  # a contractor profile stands for as many people as needed
    per_week = planning_input.constraints.working_days_per_week
    capacity = daily_capacity_tenths(resource.weekly_capacity_hours, per_week)
    load = daily_load(planning_input, plan, resource, blocks)
    return sum(max(0, tenths - capacity) for tenths in load) / TENTHS


def plan_cost(
    planning_input: PlanningInput,
    resources: Mapping[str, Resource],
    plan: Plan,
    blocks: Mapping[str, list[Block]],
) -> int:
    """Cents: each assigned task's remaining hours at the person's pace x rate, plus the
    overtime premium."""
    tasks = {task.id: task for task in planning_input.tasks}
    regular = sum(
        round(
            work_hours(tasks[scheduled.task_id], resources[scheduled.resource_id])
            * resources[scheduled.resource_id].rate_cents
        )
        for scheduled in plan.tasks.values()
        if scheduled.resource_id is not None and scheduled.resource_id in resources
    )
    premium = sum(
        round(
            overtime_hours(planning_input, plan, resource, blocks)
            * (resource.overtime_rate_cents - resource.rate_cents)
        )
        for resource in resources.values()
        if resource.overtime_rate_cents > resource.rate_cents
    )
    return regular + premium


def reserve_cents(budget_cents: int, contingency_pct: float) -> int:
    """The reserve held back from a budget: its percent, rounded up to the cent."""
    hundredths = round(min(max(contingency_pct, 0.0), 100.0) * 100)
    return -(-budget_cents * hundredths // 10_000)


def spend_limit_cents(constraints: Constraints) -> int | None:
    """What a plan may cost: the budget less its reserve (None without a budget)."""
    budget = constraints.budget_total_cents
    if budget is None:
        return None
    return budget - reserve_cents(budget, constraints.contingency_pct)
