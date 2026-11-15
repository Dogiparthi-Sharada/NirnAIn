"""Baseline ("before optimization") plan, plan.md §6.5, and where planning starts.

Purpose: Show what the plan looks like without optimizing: every task starts as early as its
dependencies allow, keeps its current owner at its WBS duration, capacity ignored. Re-plans start
from today (round-2 D4): unfinished work never starts before `planning_start` and finished work
keeps the dates of the plan in force (`done_span`), so links to it are already met.
How it fits: Built by problem.py; the solver charges for starting later than it and the
infeasibility texts use it as the earliest the chain can finish; layer: optimization.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date, timedelta

from app.optimization.calendar import workdays_through
from app.optimization.model import Dependency, PlanningInput, Task
from app.optimization.outcome import Plan, ScheduledTask


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


def task_dependencies(planning_input: PlanningInput) -> list[Dependency]:
    """Dependencies between two unfinished tasks of the input (others are ignored)."""
    open_ids = {task.id for task in planning_input.tasks if not task.done}
    return [
        link
        for link in planning_input.dependencies
        if link.predecessor_id in open_ids
        and link.successor_id in open_ids
        and link.predecessor_id != link.successor_id
    ]


def _workdays_before(planning_input: PlanningInput, day: date) -> int:
    """Working days from the project start up to, not including, `day`."""
    per_week = planning_input.constraints.working_days_per_week
    return workdays_through(planning_input.start_date, day - timedelta(days=1), per_week)


def planning_start(planning_input: PlanningInput) -> int:
    """First working-day index unfinished work may use: today, or the start if that is later."""
    return (
        0
        if planning_input.today is None
        else _workdays_before(planning_input, planning_input.today)
    )


def done_span(planning_input: PlanningInput, task: Task) -> tuple[int, int]:
    """Working days [start, end) of finished work: its dates in the plan in force.

    Without them (no plan in force) it is marked as finished, with no length, where
    planning starts.
    """
    if task.applied_start is None or task.applied_end is None:
        release = planning_start(planning_input)
        return release, release
    start = _workdays_before(planning_input, task.applied_start)
    end = workdays_through(
        planning_input.start_date,
        task.applied_end,
        planning_input.constraints.working_days_per_week,
    )
    return start, max(end, start)


def earliest_starts(planning_input: PlanningInput) -> dict[str, int]:
    """Longest-path earliest starts of unfinished work (Bellman-Ford style, so cycles
    can't hang it), none before `planning_start`."""
    duration = {task.id: task.duration_days for task in planning_input.tasks if not task.done}
    links = task_dependencies(planning_input)
    start = dict.fromkeys(duration, planning_start(planning_input))
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


def baseline_plan(planning_input: PlanningInput) -> Plan:
    """Current owners at the WBS durations, as early as dependencies allow; done work fixed."""
    employees = {employee.id for employee in planning_input.employees}
    start = earliest_starts(planning_input)
    plan = {}
    for task in planning_input.tasks:
        begin, end = (
            done_span(planning_input, task)
            if task.done
            else (start[task.id], start[task.id] + task.duration_days)
        )
        owner = task.owner_id if task.owner_id in employees else None
        plan[task.id] = ScheduledTask(task_id=task.id, start=begin, end=end, resource_id=owner)
    return Plan(tasks=plan)
