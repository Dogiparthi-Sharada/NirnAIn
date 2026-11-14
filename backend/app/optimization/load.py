"""Daily load arithmetic shared by the solver and the explanations.

Load is measured in tenths of an hour per working day so CP-SAT can work
with integers: a task spreads its effort evenly over its duration, and a
person's daily capacity is their weekly capacity divided by the working days
per week.

How long a task takes depends on who does it (`task_mode`). Their skill
level sets their pace (`speed.py`): the effort, and the duration they need
at full availability, scale with it. Someone who can give that daily demand
works at that duration; a part-timer who can't takes ceil(effort / their
daily hours) working days instead. That is what makes cutting someone's
hours lengthen the plan and adding hours shorten it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from app.optimization.calendar import week_workdays
from app.optimization.model import PlanningInput, Task

TENTHS = 10


def effort_tenths(task: Task, pace_percent: int = 100) -> float:
    """The task's effort in tenths of an hour at a pace (percent of the WBS effort),
    rounded off float noise such as 12.3 * 10."""
    return round(task.hours * TENTHS * pace_percent / 100, 6)


def daily_tenths(task: Task, duration_days: int, pace_percent: int = 100) -> int:
    """Load per working day when the effort is spread evenly over `duration_days`."""
    return math.ceil(effort_tenths(task, pace_percent) / duration_days)


def task_daily_tenths(task: Task) -> int:
    """Load per working day at the WBS duration (the task's daily demand)."""
    return daily_tenths(task, task.duration_days)


def daily_capacity_tenths(weekly_hours: float, per_week: int) -> int:
    return round(weekly_hours * TENTHS / per_week)


def flex_capacity_tenths(capacity_tenths: int) -> int:
    """Daily load allowed under "Can flex": up to 25% over contracted hours (round-2 D2)."""
    return capacity_tenths + capacity_tenths // 4


@dataclass(frozen=True, slots=True)
class Mode:
    """How long a task takes with one candidate, and the load it puts on them per day."""

    duration_days: int
    daily_tenths: int
    stretched: bool  # longer than at full availability: the person can't give the daily demand


def paced_days(task: Task, pace_percent: int) -> int:
    """Working days the task takes at full availability and this pace (never below 1)."""
    return max(1, -(-task.duration_days * pace_percent // 100))


def task_mode(task: Task, capacity_tenths: int | None, pace_percent: int = 100) -> Mode:
    """Duration and daily load of `task` for someone with this daily capacity and pace.

    Modelling rule: the effort at the person's pace is fixed, the duration
    follows the person. `None` (a contractor profile: as many specialists
    as needed) and anyone who can give the daily demand at `paced_days`
    work at that duration. Anyone with fewer hours a day stretches it to
    ceil(effort / daily hours) working days, the effort spread evenly over
    them, which is at most their daily capacity. Someone with no hours at
    all keeps the paced duration: there is nothing to stretch to, so only
    soft capacity can give them the task.
    """
    days = paced_days(task, pace_percent)
    demand = daily_tenths(task, days, pace_percent)
    if capacity_tenths is None or capacity_tenths <= 0 or demand <= capacity_tenths:
        return Mode(days, demand, stretched=False)
    duration = math.ceil(effort_tenths(task, pace_percent) / capacity_tenths)
    return Mode(duration, daily_tenths(task, duration, pace_percent), stretched=True)


@dataclass(frozen=True, slots=True)
class Block:
    """Existing commitment: `tenths` per day on working days [lo, hi)."""

    lo: int
    hi: int
    tenths: int


def allocation_blocks(inp: PlanningInput) -> dict[str, list[Block]]:
    """Other-project allocations as per-day load blocks, per employee.

    Weeks before the project start are dropped. A week's hours are spread
    evenly over its working days. The load is capped at the person's daily
    capacity: over-commitment elsewhere is not this project's to fix, and
    would otherwise make a hard capacity constraint unsatisfiable on its own.
    """
    per_week = inp.constraints.working_days_per_week
    capacity = {
        e.id: daily_capacity_tenths(e.weekly_capacity_hours, per_week) for e in inp.employees
    }
    weekly: dict[str, dict[tuple[int, int], float]] = defaultdict(lambda: defaultdict(float))
    for a in inp.allocations:
        if a.employee_id not in capacity or a.hours <= 0:
            continue
        lo, hi = week_workdays(inp.start_date, a.week_start, per_week)
        if hi > lo:
            weekly[a.employee_id][(lo, hi)] += a.hours
    blocks: dict[str, list[Block]] = {}
    for emp, weeks in weekly.items():
        out = []
        for (lo, hi), hours in sorted(weeks.items()):
            tenths = min(math.ceil(hours * TENTHS / per_week), capacity[emp])
            if tenths > 0:
                out.append(Block(lo, hi, tenths))
        if out:
            blocks[emp] = out
    return blocks
