"""Lookups behind the report of a solved plan (`explain.build_report`).

`ReportContext` answers what the report needs about a plan: dates and weeks
of working-day indices, hours and utilization per person, whether someone
is over their daily capacity, labor cost, and which dependencies bind.
`critical_chain` walks the binding chain back from the last task. Durations
are read off the plan (a part-timer's task runs longer than its WBS
duration), so the report states what the solver actually scheduled.
"""

from __future__ import annotations

from datetime import date

from app.optimization.baseline import dependency_offset, task_dependencies
from app.optimization.calendar import week_index, workday_date
from app.optimization.load import (
    TENTHS,
    allocation_blocks,
    daily_capacity_tenths,
    daily_tenths,
)
from app.optimization.model import Plan, PlanningInput, Resource, SolveOutcome, Task


def planned_days(plan: Plan, task_id: str) -> int:
    """Working days the plan gives the task (its WBS duration, or longer for a part-timer)."""
    scheduled = plan.tasks[task_id]
    return scheduled.end - scheduled.start


class ReportContext:
    """Lookups shared by every part of the report (tasks, resources, calendar, load)."""

    def __init__(self, inp: PlanningInput, outcome: SolveOutcome, plan: Plan) -> None:
        self.inp = inp
        self.plan = plan
        self.baseline = outcome.baseline
        self.per_week = inp.constraints.working_days_per_week
        self.tasks = {task.id: task for task in inp.tasks}
        self.resources = {resource.id: resource for resource in outcome.resources}
        self.blocks = allocation_blocks(inp)
        self.deps = task_dependencies(inp)

    def date_of(self, index: int) -> date:
        return workday_date(self.inp.start_date, index, self.per_week)

    def week_of(self, index: int) -> int:
        return week_index(self.inp.start_date, self.date_of(index))

    def weeks(self, start: int, end: int) -> tuple[int, int]:
        return self.week_of(start), self.week_of(max(end - 1, start))

    def hours(self, plan: Plan, resource_id: str) -> float:
        return sum(
            self.tasks[scheduled.task_id].hours
            for scheduled in plan.tasks.values()
            if scheduled.resource_id == resource_id
        )

    def other_hours(self, resource_id: str, span: int) -> float:
        """Hours committed to other projects inside working days [0, span)."""
        total = 0.0
        for block in self.blocks.get(resource_id, []):
            days = max(0, min(block.hi, span) - block.lo)
            total += days * block.tenths / TENTHS
        return total

    def utilization(self, plan: Plan, res: Resource) -> int:
        span = plan.makespan
        if span <= 0:
            return 0
        available = res.weekly_capacity_hours * span / self.per_week
        if available <= 0:
            return 0
        used = self.hours(plan, res.id) + self.other_hours(res.id, span)
        pct = round(100 * used / available)
        return min(pct, 100) if res.kind == "contractor" else pct

    def over_allocated(self, plan: Plan, resource_id: str) -> bool:
        """Whether the person's load exceeds their daily capacity on any working day.

        Each task's effort is spread over the days the plan gives it, as in the solver.
        """
        res = self.resources.get(resource_id)
        if res is None or res.kind != "employee":
            return False
        capacity = daily_capacity_tenths(res.weekly_capacity_hours, self.per_week)
        span = plan.makespan
        load = [0] * span
        for block in self.blocks.get(resource_id, []):
            for day in range(block.lo, min(block.hi, span)):
                load[day] += block.tenths
        for scheduled in plan.tasks.values():
            if scheduled.resource_id == resource_id:
                demand = daily_tenths(
                    self.tasks[scheduled.task_id], planned_days(plan, scheduled.task_id)
                )
                for day in range(scheduled.start, scheduled.end):
                    load[day] += demand
        return any(tenths > capacity for tenths in load)

    def name(self, resource_id: str | None) -> str:
        if resource_id is None:
            return "Unassigned"
        res = self.resources.get(resource_id)
        return res.name if res else "Unassigned"

    def cost(self, plan: Plan) -> int:
        return sum(
            round(
                self.tasks[scheduled.task_id].hours
                * self.resources[scheduled.resource_id].rate_cents
            )
            for scheduled in plan.tasks.values()
            if scheduled.resource_id is not None and scheduled.resource_id in self.resources
        )

    def tight_predecessors(self, plan: Plan, task_id: str) -> list[str]:
        """Predecessors whose dependency holds with no slack in `plan` (planned durations)."""
        binding = []
        for link in self.deps:
            if link.successor_id != task_id:
                continue
            offset = dependency_offset(
                link,
                planned_days(plan, link.predecessor_id),
                planned_days(plan, link.successor_id),
            )
            if plan.tasks[task_id].start == plan.tasks[link.predecessor_id].start + offset:
                binding.append(link.predecessor_id)
        return binding


def critical_chain(ctx: ReportContext, plan: Plan) -> list[Task]:
    """Walk back from the last task through binding dependencies or the same assignee."""
    if not plan.tasks:
        return []
    current = max(plan.tasks.values(), key=lambda last: (last.end, -last.start)).task_id
    chain = [current]
    seen = {current}
    while True:
        scheduled = plan.tasks[current]
        before = [
            task_id for task_id in ctx.tight_predecessors(plan, current) if task_id not in seen
        ]
        if not before:
            before = [
                other.task_id
                for other in plan.tasks.values()
                if other.task_id not in seen
                and other.resource_id is not None
                and other.resource_id == scheduled.resource_id
                and other.end == scheduled.start
            ]
        if not before or scheduled.start == 0:
            break
        current = before[0]
        chain.append(current)
        seen.add(current)
    return [ctx.tasks[task_id] for task_id in reversed(chain)]
