"""What a plan costs: everyone's hours at their rate, plus the overtime premium.

Round-2 decision D1: employees and contractors both count toward the
budget, and costs come only from people's hours x rate (infrastructure
isn't modelled). Overtime, the hours a person works above their daily
capacity, is billed at 1.5x an employee's cost and at a contractor's own
rate (`Resource.overtime_rate_cents`). Every hour is already counted once
at the regular rate, so overtime adds `overtime rate - rate` per hour.
The report (`report_context.py`) and the remedies (`remedies.py`) price
plans with these functions, so the numbers on screen agree.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.optimization.load import TENTHS, Block, daily_capacity_tenths, daily_tenths
from app.optimization.model import Plan, PlanningInput, Resource


def daily_load(
    inp: PlanningInput, plan: Plan, resource_id: str, blocks: Mapping[str, list[Block]]
) -> list[int]:
    """Tenths of an hour the person works on each working day of the plan.

    Bookings on other projects count, and each task's effort is spread over the
    days the plan gives it, as in the solver. Finished work has no load left.
    """
    tasks = {task.id: task for task in inp.tasks}
    span = plan.makespan
    load = [0] * span
    for block in blocks.get(resource_id, []):
        for day in range(block.lo, min(block.hi, span)):
            load[day] += block.tenths
    for scheduled in plan.tasks.values():
        task = tasks[scheduled.task_id]
        if scheduled.resource_id != resource_id or task.done:
            continue
        demand = daily_tenths(task, scheduled.end - scheduled.start)
        for day in range(scheduled.start, scheduled.end):
            load[day] += demand
    return load


def overtime_hours(
    inp: PlanningInput, plan: Plan, resource: Resource, blocks: Mapping[str, list[Block]]
) -> float:
    """Hours the person works above their daily capacity over the whole plan."""
    if resource.kind != "employee":
        return 0.0  # a contractor profile stands for as many people as needed
    per_week = inp.constraints.working_days_per_week
    capacity = daily_capacity_tenths(resource.weekly_capacity_hours, per_week)
    load = daily_load(inp, plan, resource.id, blocks)
    return sum(max(0, tenths - capacity) for tenths in load) / TENTHS


def plan_cost(
    inp: PlanningInput,
    resources: Mapping[str, Resource],
    plan: Plan,
    blocks: Mapping[str, list[Block]],
) -> int:
    """Cents: each assigned task's remaining hours x rate, plus the overtime premium."""
    tasks = {task.id: task for task in inp.tasks}
    regular = sum(
        round(tasks[scheduled.task_id].hours * resources[scheduled.resource_id].rate_cents)
        for scheduled in plan.tasks.values()
        if scheduled.resource_id is not None and scheduled.resource_id in resources
    )
    premium = sum(
        round(
            overtime_hours(inp, plan, resource, blocks)
            * (resource.overtime_rate_cents - resource.rate_cents)
        )
        for resource in resources.values()
        if resource.overtime_rate_cents > resource.rate_cents
    )
    return regular + premium
