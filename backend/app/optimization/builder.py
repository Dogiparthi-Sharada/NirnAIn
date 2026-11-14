"""The CP-SAT variables and constraints of the formulation documented in `solver.py`.

`ModelBuilder(problem, explain=False)` builds the main model (objective,
hard constraints). `explain=True` builds the explanation model: no
objective, and every hard constraint group behind an assumption literal
(`groups`) so `solver.explain_infeasibility` can find the groups that
conflict. Both read the same `Problem` (`problem.py`); the conflict texts
come from `conflicts.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from ortools.sat.python import cp_model

from app.optimization import conflicts
from app.optimization.model import Conflict, Resource, Task
from app.optimization.problem import Problem

# Objective weights, in cents.
MAKESPAN_CENTS_PER_DAY = 20_000
SHIFT_CENTS_PER_DAY = 1_000
CHANGE_OWNER_CENTS = 50_000
SKILL_GAP_CENTS_PER_LEVEL = 200_000
BUDGET_UNIT_CENTS = 100_000  # soft budget penalty is per $1,000 over


@dataclass(slots=True)
class Variables:
    """The decisions read back from a solution."""

    start: dict[str, cp_model.IntVar]  # task id -> first working day
    end: dict[str, cp_model.LinearExprT]  # task id -> exclusive end, set by the chosen candidate
    assign: dict[str, dict[str, cp_model.IntVar]]  # task id -> resource id -> chosen?


@dataclass(slots=True)
class Load:
    """One employee candidate's claim on that person's daily capacity."""

    interval: cp_model.IntervalVar  # present iff the candidate is chosen
    daily_tenths: int
    duration_days: int
    overtime: cp_model.IntVar | None  # tenths per day above capacity (soft / explanation)


class ModelBuilder:
    """Builds the main model or the explanation model of one `Problem`."""

    def __init__(self, problem: Problem, *, explain: bool) -> None:
        self.problem = problem
        self.constraints = problem.inp.constraints
        self.explain = explain
        self.model = cp_model.CpModel()
        self.groups: list[tuple[cp_model.IntVar, Conflict]] = []
        self.kinds: dict[int, str] = {}  # literal index -> group kind
        self._literals: dict[tuple[str, ...], cp_model.IntVar] = {}
        self._terms: list[cp_model.LinearExprT] = []
        self._loads: dict[str, list[Load]] = {}  # employee id -> candidate loads

    def _guard(
        self,
        constraint: cp_model.Constraint,
        key: tuple[str, ...],
        conflict: Callable[[], Conflict],
    ) -> None:
        """In explanation mode, make `constraint` conditional on its group's literal."""
        if not self.explain:
            return
        literal = self._literals.get(key)
        if literal is None:
            literal = self.model.new_bool_var("|".join(key))
            self._literals[key] = literal
            self.groups.append((literal, conflict()))
            self.kinds[literal.index] = key[0]
        constraint.only_enforce_if(literal)

    def _penalize(self, term: cp_model.LinearExprT) -> None:
        if not self.explain:
            self._terms.append(term)

    def _capacity_group(self, resource: Resource) -> tuple[tuple[str, ...], Callable[[], Conflict]]:
        return ("capacity", resource.id), partial(conflicts.capacity, self.problem, resource)

    def build(self) -> Variables:
        """Add every variable and constraint; the main model also gets its objective."""
        problem, model = self.problem, self.model
        variables = Variables(start={}, end={}, assign={})
        for task in problem.tasks.values():
            self._task(task, variables)
        self._capacity()
        self._dependencies(variables)
        makespan = model.new_int_var(0, problem.horizon, "makespan")
        for task_end in variables.end.values():
            model.add(makespan >= task_end)
        self._penalize(makespan * MAKESPAN_CENTS_PER_DAY)
        self._deadlines(variables, makespan)
        self._budget(variables.assign)
        if not self.explain:
            model.minimize(cp_model.LinearExpr.sum(self._terms))
        return variables

    # -------------------------------------------------------------- tasks
    def _task(self, task: Task, variables: Variables) -> None:
        """Start, candidates and end of one task; its end follows whoever is chosen."""
        problem, model = self.problem, self.model
        # Unfinished work never starts before today; the WBS duration is the shortest
        # any candidate takes.
        start = model.new_int_var(
            problem.release, problem.horizon - task.duration_days, f"start[{task.id}]"
        )
        options: dict[str, cp_model.IntVar] = {}
        ends: dict[str, tuple[cp_model.LinearExprT, int | None]] = {}
        for resource in problem.candidates[task.id]:
            gap = resource.skill_gap(task)
            if gap and self.constraints.skill_mode == "hard" and not self.explain:
                continue  # unqualified: not a candidate
            chosen = model.new_bool_var(f"assign[{task.id},{resource.id}]")
            options[resource.id] = chosen
            ends[resource.id] = self._candidate(task, resource, start, chosen)
            if gap and self.constraints.skill_mode == "hard":
                for requirement in task.skills:
                    if resource.proficiency(requirement.skill_id) < requirement.min_proficiency:
                        self._guard(
                            model.add(chosen == 0),
                            ("skill", task.id, requirement.skill_id),
                            partial(conflicts.skill, problem, task, requirement),
                        )
            elif gap:
                self._penalize(chosen * (gap * SKILL_GAP_CENTS_PER_LEVEL))
        model.add_exactly_one(options.values())
        self._baseline_terms(task, start, options)
        variables.start[task.id] = start
        variables.assign[task.id] = options
        variables.end[task.id] = self._task_end(task, start, ends, options)

    def _candidate(
        self, task: Task, resource: Resource, start: cp_model.IntVar, chosen: cp_model.IntVar
    ) -> tuple[cp_model.LinearExprT, int | None]:
        """The candidate's end and fixed duration (None when it may vary); books its load."""
        problem, model = self.problem, self.model
        mode = problem.modes[task.id, resource.id]
        if resource.kind != "employee":
            return start + mode.duration_days, mode.duration_days  # contractors: no capacity
        label = f"{task.id},{resource.id}"
        duration: int | None = mode.duration_days
        if self.explain and mode.stretched:
            # The stretch belongs to the person's capacity group: relaxing it lets
            # them work at the WBS duration, so a part-timer who makes a date
            # impossible is named in the conflict.
            days = model.new_int_var(task.duration_days, mode.duration_days, f"days[{label}]")
            end = model.new_int_var(task.duration_days, problem.horizon, f"end[{label}]")
            interval = model.new_optional_interval_var(start, days, end, chosen, f"work[{label}]")
            key, conflict = self._capacity_group(resource)
            self._guard(model.add(days == mode.duration_days), key, conflict)
            finish: cp_model.LinearExprT = end
            duration = None
        else:
            interval = model.new_optional_fixed_size_interval_var(
                start, mode.duration_days, chosen, f"work[{label}]"
            )
            finish = start + mode.duration_days
        overtime = None
        if self.explain or self.constraints.capacity_mode == "soft":
            overtime = model.new_int_var(0, mode.daily_tenths, f"overtime[{label}]")
            model.add(overtime == 0).only_enforce_if(chosen.Not())
            if not self.explain:
                model.add_hint(overtime, 0)
        self._loads.setdefault(resource.id, []).append(
            Load(interval, mode.daily_tenths, mode.duration_days, overtime)
        )
        return finish, duration

    def _task_end(
        self,
        task: Task,
        start: cp_model.IntVar,
        ends: dict[str, tuple[cp_model.LinearExprT, int | None]],
        options: dict[str, cp_model.IntVar],
    ) -> cp_model.LinearExprT:
        """`start + d` when every candidate takes d days; else channelled from the choice."""
        fixed = {duration for _, duration in ends.values() if duration is not None}
        varies = any(duration is None for _, duration in ends.values())
        if not varies and len(fixed) <= 1:
            return start + next(iter(fixed), task.duration_days)
        end = self.model.new_int_var(task.duration_days, self.problem.horizon, f"end[{task.id}]")
        for resource_id, chosen in options.items():
            self.model.add(end == ends[resource_id][0]).only_enforce_if(chosen)
        return end

    def _baseline_terms(
        self, task: Task, start: cp_model.IntVar, options: dict[str, cp_model.IntVar]
    ) -> None:
        if self.explain:
            return
        base = self.problem.baseline.tasks[task.id]
        self.model.add_hint(start, base.start)
        self._penalize((start - base.start) * SHIFT_CENTS_PER_DAY)
        owner = options.get(base.resource_id) if base.resource_id else None
        if owner is not None:
            self.model.add_hint(owner, 1)
            self._penalize(CHANGE_OWNER_CENTS - owner * CHANGE_OWNER_CENTS)

    # -------------------------------------------------------------- constraints
    def _capacity(self) -> None:
        """Per person, their tasks and other bookings within their daily capacity.

        Hard: the cumulative holds as is. Soft: each chosen task may move part
        of its daily load into overtime, paid per tenth-hour for every day it
        runs. Explanation model: overtime is free in soft mode and forbidden
        by the person's capacity literal in hard mode.
        """
        problem, model = self.problem, self.model
        per_tenth = round(self.constraints.capacity_penalty * 10)  # $ per hour -> cents per tenth
        for resource in problem.resources:
            loads = self._loads.get(resource.id)
            if not loads:
                continue
            intervals = [load.interval for load in loads]
            demands: list[cp_model.LinearExprT] = [
                load.daily_tenths if load.overtime is None else load.daily_tenths - load.overtime
                for load in loads
            ]
            for block in problem.blocks.get(resource.id, []):
                if block.lo < problem.horizon:
                    intervals.append(
                        model.new_fixed_size_interval_var(
                            block.lo, block.hi - block.lo, f"booked[{resource.id},{block.lo}]"
                        )
                    )
                    demands.append(block.tenths)
            model.add_cumulative(intervals, demands, problem.capacity[resource.id])
            overtime = [
                (load.overtime, load.duration_days) for load in loads if load.overtime is not None
            ]
            if not overtime:
                continue
            if self.constraints.capacity_mode == "soft":
                # Overtime tenths per day x the days the task runs = over-allocated
                # tenth-hours (an overestimate only when tasks partly overlap).
                self._penalize(
                    cp_model.LinearExpr.weighted_sum(
                        [variable for variable, _ in overtime],
                        [days * per_tenth for _, days in overtime],
                    )
                )
            else:
                key, conflict = self._capacity_group(resource)
                total = cp_model.LinearExpr.sum([variable for variable, _ in overtime])
                self._guard(model.add(total == 0), key, conflict)

    def _dependencies(self, variables: Variables) -> None:
        problem = self.problem
        for link in problem.dependencies:
            predecessor = problem.tasks[link.predecessor_id]
            successor = problem.tasks[link.successor_id]
            # FS/FF wait for the predecessor's finish, SS/SF for its start; FF/SF
            # bound the successor's finish, FS/SS its start. Real ends, so a
            # stretched predecessor pushes its successors.
            before = (
                variables.end[predecessor.id]
                if link.type in ("FS", "FF")
                else variables.start[predecessor.id]
            )
            after = (
                variables.end[successor.id]
                if link.type in ("FF", "SF")
                else variables.start[successor.id]
            )
            self._guard(
                self.model.add(after >= before + link.lag_days),
                ("dependency", predecessor.id, successor.id),
                partial(conflicts.dependency, link, predecessor, successor),
            )

    def _deadlines(self, variables: Variables, makespan: cp_model.IntVar) -> None:
        problem, model = self.problem, self.model
        per_day = round(self.constraints.deadline_penalty_per_day * 100)
        if problem.deadline is not None:
            if self.constraints.deadline_mode == "hard":
                self._guard(
                    model.add(makespan <= problem.deadline),
                    ("deadline",),
                    partial(conflicts.deadline, problem),
                )
            elif not self.explain:
                late = model.new_int_var(0, problem.horizon, "late")
                model.add(late >= makespan - problem.deadline)
                self._penalize(late * per_day)
        for milestone in problem.inp.milestones:
            due = problem.milestone_ends.get(milestone.id)
            if due is None:
                continue
            tasks = [
                problem.tasks[task_id] for task_id in milestone.task_ids if task_id in problem.tasks
            ]
            if milestone.mode == "hard":
                for task in tasks:
                    self._guard(
                        model.add(variables.end[task.id] <= due),
                        ("milestone", milestone.id),
                        partial(conflicts.milestone, problem, milestone, tasks),
                    )
            elif not self.explain:
                late = model.new_int_var(0, problem.horizon, f"late[{milestone.id}]")
                for task in tasks:
                    model.add(late >= variables.end[task.id] - due)
                self._penalize(late * per_day)

    def _budget(self, assign: dict[str, dict[str, cp_model.IntVar]]) -> None:
        problem, model = self.problem, self.model
        pairs = [
            (chosen, problem.cost[task_id, resource_id])
            for task_id, options in assign.items()
            for resource_id, chosen in options.items()
        ]
        cost = cp_model.LinearExpr.weighted_sum(
            [chosen for chosen, _ in pairs], [cents for _, cents in pairs]
        )
        self._penalize(cost)
        worst = sum(
            max((problem.cost[task_id, resource.id] for resource in candidates), default=0)
            for task_id, candidates in problem.candidates.items()
        )
        for key, label, cap in (
            ("budget", "Budget cap", self.constraints.budget_total_cents),
            ("labor", "Labor cap", self.constraints.labor_cap_cents),
        ):
            if cap is None:
                continue
            if self.constraints.budget_mode == "hard":
                self._guard(
                    model.add(cost <= cap),
                    (key,),
                    partial(conflicts.budget, problem, label, cap),
                )
            elif not self.explain:
                over = model.new_int_var(0, worst // BUDGET_UNIT_CENTS + 1, f"over[{key}]")
                model.add(over * BUDGET_UNIT_CENTS >= cost - cap)
                self._penalize(over * round(self.constraints.budget_penalty * 100))
