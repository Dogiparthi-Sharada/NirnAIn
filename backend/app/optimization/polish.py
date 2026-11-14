"""Drop overtime that buys nothing (round 3, R7).

Purpose: Under "Can flex" the main model pays for overtime but also rewards an earlier finish,
so plans booked overtime no date needed. Overtime is worth booking only when it keeps a date the
PM set or lowers the cost, so when the solved plan books overtime the model is solved again with
"Must hold" capacity and what the overtime plan achieved as constraints:
    * no later on any date: lateness against go-live, the target date and every milestone;
    * no dearer: labor cost at most the overtime plan's, its premium included;
    * no worse skill fit: at most as many missing proficiency levels.
If such a plan exists it replaces the overtime plan. The re-solve may take as long as the plan
did, within the run's time limit and at least `MIN_SECONDS`; otherwise the overtime plan stands.
How it fits: Called by `solver.solve` after a successful Can-flex solve; layer: optimization.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ortools.sat.python import cp_model

from app.optimization.builder import ModelBuilder, Variables
from app.optimization.costing import overtime_hours, plan_cost
from app.optimization.outcome import Plan
from app.optimization.problem import Problem, prepare
from app.optimization.runtime import SolveControl, SolveOptions, new_solver, run_model

MIN_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class Polished:
    """The plan without overtime, and how good its solve was."""

    plan: Plan
    objective: float
    best_bound: float
    optimal: bool


def books_overtime(problem: Problem, plan: Plan) -> bool:
    """Whether anyone works above their daily capacity in `plan`."""
    return any(
        overtime_hours(problem.planning_input, plan, resource, problem.blocks) > 0
        for resource in problem.resources
    )


def _keep_achievements(
    model: cp_model.CpModel, problem: Problem, variables: Variables, plan: Plan
) -> None:
    """No later on any date, no dearer and no worse skill fit than `plan`."""
    work = [plan.tasks[task_id] for task_id in problem.tasks]
    finish = max((scheduled.end for scheduled in work), default=0)
    dates = [index for index in (problem.deadline, problem.target) if index is not None]
    if dates:
        # Lateness against the earliest of them may not grow.
        latest = max(min(dates), finish)
        for task_end in variables.end.values():
            model.add(task_end <= latest)
    for milestone in problem.planning_input.milestones:
        due = problem.milestone_ends.get(milestone.id)
        covered = [task_id for task_id in milestone.task_ids if task_id in variables.end]
        if due is None or not covered:
            continue
        latest = max(due, *(plan.tasks[task_id].end for task_id in covered))
        for task_id in covered:
            model.add(variables.end[task_id] <= latest)
    resources = {resource.id: resource for resource in problem.resources}
    choices = [
        (chosen, task_id, resource_id)
        for task_id, options in variables.assign.items()
        for resource_id, chosen in options.items()
    ]
    literals = [chosen for chosen, _, _ in choices]
    cost = [problem.cost[task_id, resource_id] for _, task_id, resource_id in choices]
    model.add(
        cp_model.LinearExpr.weighted_sum(literals, cost)
        <= plan_cost(problem.planning_input, resources, plan, problem.blocks)
    )
    gaps = [
        resources[resource_id].skill_gap(problem.tasks[task_id])
        for _, task_id, resource_id in choices
    ]
    planned_gap = sum(
        resources[scheduled.resource_id].skill_gap(problem.tasks[scheduled.task_id])
        for scheduled in work
        if scheduled.resource_id in resources
    )
    model.add(cp_model.LinearExpr.weighted_sum(literals, gaps) <= planned_gap)


def without_overtime(
    problem: Problem,
    plan: Plan,
    options: SolveOptions,
    control: SolveControl | None,
    time_limit_s: float,
) -> Polished | None:
    """The best plan without overtime that keeps what `plan` achieved, if there is one."""
    constraints = replace(problem.planning_input.constraints, capacity_mode="hard")
    strict = prepare(replace(problem.planning_input, constraints=constraints))
    builder = ModelBuilder(strict, explain=False)
    variables = builder.build()
    _keep_achievements(builder.model, strict, variables, plan)
    solver = new_solver(max(time_limit_s, MIN_SECONDS), options.workers, options.random_seed)
    status = run_model(builder.model, solver, control)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return Polished(
        variables.read(solver, strict.done),
        solver.objective_value,
        solver.best_objective_bound,
        status == cp_model.OPTIMAL,
    )
