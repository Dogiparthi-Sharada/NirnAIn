"""Each person's work in the CP-SAT model: work modes, overtime and capacity.

Purpose: Model how a person's hours shape the plan, following these rules:
    * Modes: a candidate does the task in exactly one mode, each an optional fixed-size interval
      from the task's start: "regular" at their daily hours (part-timers stretch the task);
      "flex" (Can flex only) up to 25% over their hours; "booked" stretched to the hours their
      other-project bookings leave (RES-006); in the explanation model a "relaxed" full-time
      mode, open only when the person's capacity group is dropped, so a part-timer who makes a
      date impossible is named. Every mode is at the person's pace (speed.py).
    * Capacity: a cumulative over the person's intervals and bookings against their daily
      capacity. Can flex: each interval may move part of its load into paid overtime, and a
      second cumulative caps the full load at 125% (round-2 D2).
    * Contractor profiles have no capacity: they keep the WBS duration and book nothing here.
How it fits: `ModelBuilder` (builder.py) asks `Workload.candidate` for every (task, person) and
calls `Workload.close` once to add the capacity constraints; layer: optimization.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Literal

from ortools.sat.python import cp_model

from app.optimization import conflicts
from app.optimization.load import TENTHS, Mode, flex_capacity_tenths, task_mode
from app.optimization.model import Task
from app.optimization.outcome import Conflict, Resource

if TYPE_CHECKING:
    from app.optimization.builder import ModelBuilder

ModeKind = Literal["regular", "flex", "booked", "relaxed"]


@dataclass(slots=True)
class Load:
    """One mode's claim on a person's daily capacity."""

    interval: cp_model.IntervalVar  # present iff the mode is chosen
    daily_tenths: int
    duration_days: int
    overtime: cp_model.IntVar | None  # tenths per day above capacity (Can flex / explanation)


class Workload:
    """The modes and capacity constraints of every person in one model."""

    def __init__(self, builder: ModelBuilder) -> None:
        """Attach to the builder's model and problem; capacity is flexible under "Can flex"."""
        self.builder = builder
        self.problem = builder.problem
        self.model = builder.model
        self.flexible = builder.constraints.capacity_mode == "soft"
        self._loads: dict[str, list[Load]] = {}  # employee id -> mode loads

    def capacity_group(self, resource: Resource) -> tuple[tuple[str, ...], Callable[[], Conflict]]:
        """The explanation model's group for one person's capacity."""
        return ("capacity", resource.id), partial(conflicts.capacity, self.problem, resource)

    def _modes(self, task: Task, resource: Resource) -> list[tuple[Mode, ModeKind]]:
        """Return the ways this person may do the task: regular, flex, booked and relaxed modes."""
        regular = self.problem.modes[task.id, resource.id]
        modes: list[tuple[Mode, ModeKind]] = [(regular, "regular")]
        flex = self.problem.flex_modes.get((task.id, resource.id))
        if self.flexible and flex is not None and flex.duration_days < regular.duration_days:
            modes.append((flex, "flex"))
        modes += [(mode, "booked") for mode in self.problem.booked.get((task.id, resource.id), [])]
        shortest = min(mode.duration_days for mode, _ in modes)
        relaxed = task_mode(task, None, self.problem.pace[task.id, resource.id])
        if self.builder.explain and relaxed.duration_days < shortest:
            modes.append((relaxed, "relaxed"))
        return modes

    def candidate(
        self, task: Task, resource: Resource, start: cp_model.IntVar, chosen: cp_model.IntVar
    ) -> list[tuple[cp_model.IntVar, int]]:
        """(literal, working days) of each way this person may do the task; books the loads."""
        if resource.kind != "employee":
            return [(chosen, self.problem.modes[task.id, resource.id].duration_days)]
        model = self.model
        modes = self._modes(task, resource)
        label = f"{task.id},{resource.id}"
        if len(modes) == 1:
            literals = [chosen]
        else:
            literals = [model.new_bool_var(f"{kind}[{label}]") for _, kind in modes]
            # Exactly one mode is on when this person is chosen, none otherwise.
            model.add(cp_model.LinearExpr.sum(literals) == chosen)
        for (mode, kind), literal in zip(modes, literals, strict=True):
            # Optional interval: it exists (and books capacity) only if its mode is chosen.
            interval = model.new_optional_fixed_size_interval_var(
                start, mode.duration_days, literal, f"{kind}[{label}]"
            )
            overtime = None
            if self.flexible or self.builder.explain:
                overtime = model.new_int_var(0, mode.daily_tenths, f"overtime[{kind},{label}]")
                model.add(overtime == 0).only_enforce_if(literal.Not())
                if not self.builder.explain:
                    model.add_hint(overtime, 0)
            if kind == "relaxed":
                key, conflict = self.capacity_group(resource)
                self.builder.guard(model.add(literal == 0), key, conflict)
            self._loads.setdefault(resource.id, []).append(
                Load(interval, mode.daily_tenths, mode.duration_days, overtime)
            )
        return [
            (literal, mode.duration_days)
            for (mode, _), literal in zip(modes, literals, strict=True)
        ]

    def close(self) -> None:
        """Add each person's capacity constraints and overtime terms."""
        for resource in self.problem.resources:
            loads = self._loads.get(resource.id)
            if loads:
                self._person(resource, loads)

    def _person(self, resource: Resource, loads: list[Load]) -> None:
        """Add one person's capacity constraints and overtime cost (or forbid it when hard)."""
        problem, model = self.problem, self.model
        capacity = problem.capacity[resource.id]
        bookings = [
            (
                model.new_fixed_size_interval_var(
                    block.lo, block.hi - block.lo, f"booked[{resource.id},{block.lo}]"
                ),
                block.tenths,
            )
            for block in problem.blocks.get(resource.id, [])
            if block.lo < problem.horizon
        ]
        regular: list[cp_model.LinearExprT] = [
            load.daily_tenths if load.overtime is None else load.daily_tenths - load.overtime
            for load in loads
        ]
        intervals = [load.interval for load in loads] + [interval for interval, _ in bookings]
        # Cumulative capacity: on every day the chosen modes' regular load plus the bookings
        # elsewhere stays within the person's daily hours.
        model.add_cumulative(intervals, regular + [tenths for _, tenths in bookings], capacity)
        overtime = [
            (load.overtime, load.duration_days) for load in loads if load.overtime is not None
        ]
        if self.flexible:
            self._flex_cap(resource, loads, intervals, bookings)
        if not overtime:
            return
        if self.flexible:
            # Overtime tenths per day x the days the mode runs = over-allocated
            # tenth-hours (an overestimate only when tasks partly overlap).
            per_tenth = round(self.builder.constraints.capacity_penalty * 10)  # $/h -> cents/tenth
            premium = round((resource.overtime_rate_cents - resource.rate_cents) / TENTHS)
            self.builder.penalize(
                cp_model.LinearExpr.weighted_sum(
                    [variable for variable, _ in overtime],
                    [days * (per_tenth + premium) for _, days in overtime],
                )
            )
        else:
            key, conflict = self.capacity_group(resource)
            total = cp_model.LinearExpr.sum([variable for variable, _ in overtime])
            self.builder.guard(self.model.add(total == 0), key, conflict)

    def _flex_cap(
        self,
        resource: Resource,
        loads: list[Load],
        intervals: list[cp_model.IntervalVar],
        bookings: list[tuple[cp_model.IntervalVar, int]],
    ) -> None:
        """Can flex goes up to 25% over contracted hours, never beyond (D2)."""
        model = self.model
        limit = flex_capacity_tenths(self.problem.capacity[resource.id])
        demands = [load.daily_tenths for load in loads] + [tenths for _, tenths in bookings]
        cap: cp_model.LinearExprT = limit
        if self.builder.explain:
            variable = model.new_int_var(limit, limit + sum(demands), f"flex[{resource.id}]")
            key, conflict = self.capacity_group(resource)
            self.builder.guard(model.add(variable == limit), key, conflict)
            cap = variable
        model.add_cumulative(intervals, demands, cap)
