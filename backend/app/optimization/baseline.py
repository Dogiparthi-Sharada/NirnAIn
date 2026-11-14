"""Baseline ("before optimization") plan, plan.md §6.5.

Forward pass: every task starts as early as its dependencies allow, keeps its
current owner at its WBS duration, and capacity is ignored (no levelling).
The infeasibility explanations use it as the earliest the dependency chain
can finish with everyone at the WBS durations ("even with unlimited
people"); the solver charges for starting later than it.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.optimization.model import Dependency, Plan, PlanningInput, ScheduledTask


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
    """Dependencies whose both ends are tasks in the input (others are ignored)."""
    ids = {t.id for t in inp.tasks}
    return [
        d
        for d in inp.dependencies
        if d.predecessor_id in ids and d.successor_id in ids and d.predecessor_id != d.successor_id
    ]


def earliest_starts(
    inp: PlanningInput, durations: Mapping[str, int] | None = None
) -> dict[str, int]:
    """Longest-path earliest starts (Bellman-Ford style, so cycles can't hang it).

    `durations` (task id -> working days) overrides the WBS durations, e.g.
    with each task at its fastest candidate's duration.
    """
    duration = {task.id: task.duration_days for task in inp.tasks}
    duration.update(durations or {})
    links = task_dependencies(inp)
    start = dict.fromkeys(duration, 0)
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
    employees = {e.id for e in inp.employees}
    start = earliest_starts(inp)
    return Plan(
        tasks={
            t.id: ScheduledTask(
                task_id=t.id,
                start=start[t.id],
                end=start[t.id] + t.duration_days,
                resource_id=t.owner_id if t.owner_id in employees else None,
            )
            for t in inp.tasks
        }
    )
