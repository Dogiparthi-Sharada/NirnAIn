"""Baseline ("before optimization") plan, plan.md §6.5, and where planning starts.

Forward pass: every task starts as early as its dependencies allow, keeps its
current owner at its WBS duration, and capacity is ignored (no levelling).
The infeasibility explanations use it as the earliest the dependency chain
can finish with everyone at the WBS durations ("even with unlimited
people"); the solver charges for starting later than it.

Re-plans start from today (round-2 decision D4): unfinished work never
starts before `planning_start`, and finished work keeps the dates of the
plan in force (`done_span`). Dependencies on finished work are already met,
so they are dropped.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.optimization.calendar import workdays_through
from app.optimization.model import Dependency, Plan, PlanningInput, ScheduledTask, Task


def dependency_offset(dep: Dependency, pred_duration: int, succ_duration: int) -> int:
    """`w` such that the dependency means `start(succ) >= start(pred) + w`.

    FS: succ starts after pred finishes; SS: after pred starts; FF: succ
    finishes after pred finishes; SF: succ finishes after pred starts. The
    lag (working days, may be negative) is added in every case.
    """
    match dep.type:
        case "FS":
            return pred_duration + dep.lag_days
        case "SS":
            return dep.lag_days
        case "FF":
            return pred_duration + dep.lag_days - succ_duration
        case "SF":
            return dep.lag_days - succ_duration


def task_dependencies(inp: PlanningInput) -> list[Dependency]:
    """Dependencies between two unfinished tasks of the input (others are ignored)."""
    open_ids = {task.id for task in inp.tasks if not task.done}
    return [
        link
        for link in inp.dependencies
        if link.predecessor_id in open_ids
        and link.successor_id in open_ids
        and link.predecessor_id != link.successor_id
    ]


def _workdays_before(inp: PlanningInput, day: date) -> int:
    """Working days from the project start up to, not including, `day`."""
    per_week = inp.constraints.working_days_per_week
    return workdays_through(inp.start_date, day - timedelta(days=1), per_week)


def planning_start(inp: PlanningInput) -> int:
    """First working-day index unfinished work may use: today, or the start if that is later."""
    return 0 if inp.today is None else _workdays_before(inp, inp.today)


def done_span(inp: PlanningInput, task: Task) -> tuple[int, int]:
    """Working days [start, end) of finished work: its dates in the plan in force.

    Without them (no plan in force) it is marked as finished, with no length, where
    planning starts.
    """
    if task.applied_start is None or task.applied_end is None:
        release = planning_start(inp)
        return release, release
    start = _workdays_before(inp, task.applied_start)
    end = workdays_through(inp.start_date, task.applied_end, inp.constraints.working_days_per_week)
    return start, max(end, start)


def earliest_starts(inp: PlanningInput) -> dict[str, int]:
    """Longest-path earliest starts of unfinished work (Bellman-Ford style, so cycles
    can't hang it), none before `planning_start`."""
    duration = {task.id: task.duration_days for task in inp.tasks if not task.done}
    links = task_dependencies(inp)
    start = dict.fromkeys(duration, planning_start(inp))
    for _ in range(len(duration)):
        changed = False
        for link in links:
            offset = dependency_offset(
                link, duration[link.predecessor_id], duration[link.successor_id]
            )
            candidate = start[link.predecessor_id] + offset
            if candidate > start[link.successor_id]:
                start[link.successor_id] = candidate
                changed = True
        if not changed:
            break
    return start


def baseline_plan(inp: PlanningInput) -> Plan:
    """Current owners at the WBS durations, as early as dependencies allow; done work fixed."""
    employees = {employee.id for employee in inp.employees}
    start = earliest_starts(inp)
    plan = {}
    for task in inp.tasks:
        begin, end = (
            done_span(inp, task)
            if task.done
            else (start[task.id], start[task.id] + task.duration_days)
        )
        owner = task.owner_id if task.owner_id in employees else None
        plan[task.id] = ScheduledTask(task_id=task.id, start=begin, end=end, resource_id=owner)
    return Plan(tasks=plan)
