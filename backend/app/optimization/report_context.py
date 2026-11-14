"""Lookups behind the report of a solved plan (`explain.build_report`).

`ReportContext` answers what the report needs about a plan: dates and weeks
of working-day indices, hours, utilization and the busiest day per person,
whether someone is over their daily capacity, labor cost, and which
dependencies bind.
`critical_chain` walks the binding chain back from the last task. Durations
are read off the plan (a part-timer's task runs longer than its WBS
duration), so the report states what the solver actually scheduled.
"""

from __future__ import annotations

from datetime import date

from app.optimization.baseline import dependency_offset, task_dependencies
from app.optimization.calendar import week_index, workday_date
from app.optimization.costing import daily_load, plan_cost
from app.optimization.load import TENTHS, allocation_blocks, daily_capacity_tenths, task_mode
from app.optimization.model import Plan, PlanningInput, Resource, SolveOutcome, Task
from app.optimization.speed import pace_percent, work_hours


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

    def paced(self, plan: Plan) -> bool:
        """Whether `plan` runs at each person's pace (D5). The baseline is the plan as
        written: current owners at the WBS durations and effort."""
        return plan is not self.baseline

    def hours(self, plan: Plan, resource_id: str) -> float:
        """Hours of work the person has in `plan` (at their pace, `speed.work_hours`)."""
        resource = self.resources.get(resource_id)
        if resource is None:
            return 0.0
        return sum(
            work_hours(self.tasks[scheduled.task_id], resource)
            if self.paced(plan)
            else self.tasks[scheduled.task_id].hours
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
        """Whether the person's load exceeds their daily capacity on any working day."""
        res = self.resources.get(resource_id)
        if res is None or res.kind != "employee":
            return False
        capacity = daily_capacity_tenths(res.weekly_capacity_hours, self.per_week)
        load = daily_load(self.inp, plan, res, self.blocks, self.paced(plan))
        return any(tenths > capacity for tenths in load)

    def peak_percent(self, plan: Plan, res: Resource) -> int:
        """The person's busiest working day in `plan`: that day's load, bookings on other
        projects included, as a percent of their daily hours (0 without hours)."""
        capacity = daily_capacity_tenths(res.weekly_capacity_hours, self.per_week)
        if capacity <= 0:
            return 0
        load = daily_load(self.inp, plan, res, self.blocks, self.paced(plan))
        return round(100 * max(load, default=0) / capacity)

    def hours_label(self, task: Task, resource: Resource, days: int) -> str:
        """'40h/week', and 'less other projects' bookings' when the plan stretches the task
        beyond what those hours alone need (`load.booked_modes`)."""
        hours = f"{resource.weekly_capacity_hours:g}h/week"
        capacity = daily_capacity_tenths(resource.weekly_capacity_hours, self.per_week)
        if days > task_mode(task, capacity, pace_percent(resource, task)).duration_days:
            return f"{hours} less other projects' bookings"
        return hours

    def name(self, resource_id: str | None) -> str:
        if resource_id is None:
            return "Unassigned"
        res = self.resources.get(resource_id)
        return res.name if res else "Unassigned"

    def cost(self, plan: Plan) -> int:
        """Labor cost of the plan, overtime premium included (`costing.plan_cost`)."""
        return plan_cost(self.inp, self.resources, plan, self.blocks)

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
