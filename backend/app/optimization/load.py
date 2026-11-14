"""Daily load arithmetic shared by the solver and the explanations.

Purpose: Load is measured in tenths of an hour per working day so CP-SAT works with integers. How
long a task takes depends on who does it (`task_mode`): skill sets the pace, and a part-timer who
can't give the daily demand takes ceil(effort / their daily hours) days, so cutting someone's hours
lengthens the plan; bookings elsewhere may stretch a task the same way (`booked_modes`).
How it fits: Used by problem.py, workload.py, report_context.py and conflicts.py;
layer: optimization.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from app.optimization.calendar import week_workdays
from app.optimization.model import PlanningInput, Task

TENTHS = 10
# The longer ways to do a task beside bookings, per task and person (`booked_modes`).
MAX_BOOKED_MODES = 3


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
    """Return a person's daily capacity in tenth-hours: weekly hours over working days per week."""
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


def booked_modes(
    task: Task, capacity_tenths: int | None, booked: Sequence[Block], pace_percent: int = 100
) -> list[Mode]:
    """The longer ways to do `task` beside the person's bookings on other projects.

    Modelling rule (QA pack RES-006): a booking leaves the person their daily
    capacity less the booked load on the days it covers. When that is less
    than the task's regular daily load, the task may stretch to it as a
    part-timer's does (`task_mode`), so it runs beside the booking at the
    hours left instead of only waiting for the booking to end. One mode per
    resulting duration, shortest first; at most `MAX_BOOKED_MODES`: the
    shortest ones and always the longest, which fits beside every booking.
    Contractor profiles (capacity None) carry no bookings.
    """
    if capacity_tenths is None:
        return []
    regular = task_mode(task, capacity_tenths, pace_percent)
    stretches: dict[int, Mode] = {}
    for left in {capacity_tenths - block.tenths for block in booked}:
        if 0 < left < regular.daily_tenths:
            mode = task_mode(task, left, pace_percent)
            stretches[mode.duration_days] = mode
    found = [stretches[days] for days in sorted(stretches)]
    if len(found) > MAX_BOOKED_MODES:
        found = [*found[: MAX_BOOKED_MODES - 1], found[-1]]
    return found


def allocation_blocks(planning_input: PlanningInput) -> dict[str, list[Block]]:
    """Other-project allocations as per-day load blocks, per employee.

    Weeks before the project start are dropped. A week's hours are spread
    evenly over its working days. The load is capped at the person's daily
    capacity: over-commitment elsewhere is not this project's to fix, and
    would otherwise make a hard capacity constraint unsatisfiable on its own.
    """
    per_week = planning_input.constraints.working_days_per_week
    capacity = {
        employee.id: daily_capacity_tenths(employee.weekly_capacity_hours, per_week)
        for employee in planning_input.employees
    }
    weekly: dict[str, dict[tuple[int, int], float]] = defaultdict(lambda: defaultdict(float))
    for allocation in planning_input.allocations:
        if allocation.employee_id not in capacity or allocation.hours <= 0:
            continue
        lo, hi = week_workdays(planning_input.start_date, allocation.week_start, per_week)
        if hi > lo:
            weekly[allocation.employee_id][(lo, hi)] += allocation.hours
    blocks: dict[str, list[Block]] = {}
    for employee_id, weeks in weekly.items():
        out = []
        for (lo, hi), hours in sorted(weeks.items()):
            tenths = min(math.ceil(hours * TENTHS / per_week), capacity[employee_id])
            if tenths > 0:
                out.append(Block(lo, hi, tenths))
        if out:
            blocks[employee_id] = out
    return blocks
