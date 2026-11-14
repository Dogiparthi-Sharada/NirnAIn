"""The CP-SAT variables and constraints of the formulation documented in `solver.py`.

Purpose: Build the main model (objective, hard constraints) or, with `explain=True`, the explanation
model: no objective and every hard constraint group behind an assumption literal (`groups`) so
`blockers.explain_infeasibility` can find the groups that conflict.
How it fits: Both read the same `Problem` (problem.py); each person's work modes and capacity are in
workload.py; the conflict texts come from conflicts.py; layer: optimization.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial

from ortools.sat.python import cp_model

from app.optimization import conflicts
from app.optimization.costing import spend_limit_cents
from app.optimization.model import Task
from app.optimization.outcome import Conflict, Plan, ScheduledTask
from app.optimization.problem import Problem
from app.optimization.workload import Workload

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

    def read(self, solver: cp_model.CpSolver, done: Mapping[str, ScheduledTask]) -> Plan:
        """The solution as a plan: finished work where it happened, the rest as solved."""
        plan = dict(done)
        for task_id, choices in self.assign.items():
            chosen = next(
                resource_id
                for resource_id, assigned in choices.items()
                if solver.boolean_value(assigned)
            )
            plan[task_id] = ScheduledTask(
                task_id, solver.value(self.start[task_id]), solver.value(self.end[task_id]), chosen
            )
        return Plan(plan)


class ModelBuilder:
    """Builds the main model or the explanation model of one `Problem`."""

    def __init__(self, problem: Problem, *, explain: bool) -> None:
        """Start an empty model for the problem; `explain` selects the explanation model."""
        self.problem = problem
        self.constraints = problem.planning_input.constraints
        self.explain = explain
        self.model = cp_model.CpModel()
        self.groups: list[tuple[cp_model.IntVar, Conflict]] = []
        self.kinds: dict[int, str] = {}  # literal index -> group kind
        self._literals: dict[tuple[str, ...], cp_model.IntVar] = {}
        self._terms: list[cp_model.LinearExprT] = []
        self.workload = Workload(self)

    def guard(
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

    def penalize(self, term: cp_model.LinearExprT) -> None:
        """Add an objective term (the explanation model has no objective)."""
        if not self.explain:
            self._terms.append(term)

    def build(self) -> Variables:
        """Add every variable and constraint; the main model also gets its objective."""
        problem, model = self.problem, self.model
        variables = Variables(start={}, end={}, assign={})
        for task in problem.tasks.values():
            self._task(task, variables)
        self.workload.close()
        self._dependencies(variables)
        makespan = model.new_int_var(0, problem.horizon, "makespan")
        for task_end in variables.end.values():
            model.add(makespan >= task_end)
        self.penalize(makespan * MAKESPAN_CENTS_PER_DAY)
        self._deadlines(variables, makespan)
        self._budget(variables.assign)
        if not self.explain:
            model.minimize(cp_model.LinearExpr.sum(self._terms))
        return variables

    # -------------------------------------------------------------- tasks
    def _task(self, task: Task, variables: Variables) -> None:
        """Start, candidates and end of one task; its end follows whoever is chosen."""
        problem, model = self.problem, self.model
        # Unfinished work never starts before today, and ends by the horizon even with
        # the fastest candidate (a skilled person may beat the WBS duration, D5).
        shortest = min(
            (
                problem.modes[task.id, resource.id].duration_days
                for resource in problem.candidates[task.id]
            ),
            default=task.duration_days,
        )
        start = model.new_int_var(problem.release, problem.horizon - shortest, f"start[{task.id}]")
        options: dict[str, cp_model.IntVar] = {}
        ends: list[tuple[cp_model.IntVar, int]] = []
        for resource in problem.candidates[task.id]:
            gap = resource.skill_gap(task)
            if gap and self.constraints.skill_mode == "hard" and not self.explain:
                continue  # unqualified: not a candidate
            chosen = model.new_bool_var(f"assign[{task.id},{resource.id}]")
            options[resource.id] = chosen
            ends += self.workload.candidate(task, resource, start, chosen)
            if gap and self.constraints.skill_mode == "hard":
                # Explanation model: the unqualified candidate stays but is forbidden behind
                # the skill's literal, so a missing skill can be named as a blocker.
                for requirement in task.skills:
                    if resource.proficiency(requirement.skill_id) < requirement.min_proficiency:
                        self.guard(
                            model.add(chosen == 0),
                            ("skill", task.id, requirement.skill_id),
                            partial(conflicts.skill, problem, task, requirement),
                        )
            elif gap:
                self.penalize(chosen * (gap * SKILL_GAP_CENTS_PER_LEVEL))
        # Exactly one candidate does the task; its literal switches on that person's intervals.
        model.add_exactly_one(options.values())
        self._baseline_terms(task, start, options)
        variables.start[task.id] = start
        variables.assign[task.id] = options
        variables.end[task.id] = self._task_end(task, start, ends)

    def _task_end(
        self, task: Task, start: cp_model.IntVar, ends: list[tuple[cp_model.IntVar, int]]
    ) -> cp_model.LinearExprT:
        """`start + d` when every way to do it takes d days; else set by the chosen way."""
        durations = {days for _, days in ends}
        if len(durations) <= 1:
            return start + next(iter(durations), task.duration_days)
        end = self.model.new_int_var(min(durations), self.problem.horizon, f"end[{task.id}]")
        for literal, days in ends:
            self.model.add(end == start + days).only_enforce_if(literal)
        return end

    def _baseline_terms(
        self, task: Task, start: cp_model.IntVar, options: dict[str, cp_model.IntVar]
    ) -> None:
        """Hint the baseline start and owner, and charge for drifting from them (main model only).

        Moving a task off its current owner or later than its baseline start costs money, which
        keeps
        recommendations few and explainable.
        """
        if self.explain:
            return
        base = self.problem.baseline.tasks[task.id]
        self.model.add_hint(start, base.start)
        self.penalize((start - base.start) * SHIFT_CENTS_PER_DAY)
        owner = options.get(base.resource_id) if base.resource_id else None
        if owner is not None:
            self.model.add_hint(owner, 1)
            # Costs CHANGE_OWNER_CENTS unless the current owner keeps the task.
            self.penalize(CHANGE_OWNER_CENTS - owner * CHANGE_OWNER_CENTS)

    # -------------------------------------------------------------- constraints
    def _dependencies(self, variables: Variables) -> None:
        """Add each FS/SS/FF/SF link with its lag on the real starts and ends."""
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
            self.guard(
                self.model.add(after >= before + link.lag_days),
                ("dependency", predecessor.id, successor.id),
                partial(conflicts.dependency, link, predecessor, successor),
            )

    def _deadlines(self, variables: Variables, makespan: cp_model.IntVar) -> None:
        """Add the go-live deadline, phase milestones and the target date, hard or as penalties."""
        problem, model = self.problem, self.model
        per_day = round(self.constraints.deadline_penalty_per_day * 100)
        keep = round(self.constraints.target_penalty_per_day * 100)
        if problem.target is not None and keep > 0 and not self.explain:
            # A remedy's run tries hard to keep the target date (round-2 D2).
            behind = model.new_int_var(0, problem.horizon, "late[target]")
            model.add(behind >= makespan - problem.target)
            self.penalize(behind * keep)
        if problem.deadline is not None:
            if self.constraints.deadline_mode == "hard":
                self.guard(
                    model.add(makespan <= problem.deadline),
                    ("deadline",),
                    partial(conflicts.deadline, problem),
                )
            elif not self.explain:
                # Soft deadline: `late` (>= 0) covers the days past it; each one is paid.
                late = model.new_int_var(0, problem.horizon, "late")
                model.add(late >= makespan - problem.deadline)
                self.penalize(late * per_day)
        for milestone in problem.planning_input.milestones:
            due = problem.milestone_ends.get(milestone.id)
            if due is None:
                continue
            tasks = [
                problem.tasks[task_id] for task_id in milestone.task_ids if task_id in problem.tasks
            ]
            if milestone.mode == "hard":
                for task in tasks:
                    self.guard(
                        model.add(variables.end[task.id] <= due),
                        ("milestone", milestone.id),
                        partial(conflicts.milestone, problem, milestone, tasks),
                    )
            elif not self.explain:
                late = model.new_int_var(0, problem.horizon, f"late[{milestone.id}]")
                for task in tasks:
                    model.add(late >= variables.end[task.id] - due)
                self.penalize(late * per_day)

    def _budget(self, assign: dict[str, dict[str, cp_model.IntVar]]) -> None:
        """Charge the labor cost and cap it by the budget (less the reserve) and the labor cap.

        Hard mode adds the caps as constraints; soft mode pays `budget_penalty` per $1,000 over.
        """
        problem, model = self.problem, self.model
        pairs = [
            (chosen, problem.cost[task_id, resource_id])
            for task_id, options in assign.items()
            for resource_id, chosen in options.items()
        ]
        cost = cp_model.LinearExpr.weighted_sum(
            [chosen for chosen, _ in pairs], [cents for _, cents in pairs]
        )
        self.penalize(cost)
        worst = sum(
            max((problem.cost[task_id, resource.id] for resource in candidates), default=0)
            for task_id, candidates in problem.candidates.items()
        )
        limit, labor_cap = spend_limit_cents(self.constraints), self.constraints.labor_cap_cents
        # The reserve is held back (R5): the hard cap, and the soft penalty's start, is
        # the budget less the reserve. The labor cap has no reserve.
        for key, label, amount, cap in (
            ("budget", "Budget cap", self.constraints.budget_total_cents, limit),
            ("labor", "Labor cap", labor_cap, labor_cap),
        ):
            if amount is None or cap is None:
                continue
            if self.constraints.budget_mode == "hard":
                self.guard(
                    model.add(cost <= cap),
                    (key,),
                    partial(conflicts.budget, problem, label, amount, amount - cap),
                )
            elif not self.explain:
                # Soft budget: `over` counts the $1,000 units above the cap, each one paid.
                over = model.new_int_var(0, worst // BUDGET_UNIT_CENTS + 1, f"over[{key}]")
                model.add(over * BUDGET_UNIT_CENTS >= cost - cap)
                self.penalize(over * round(self.constraints.budget_penalty * 100))
