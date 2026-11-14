"""Drop overtime that buys nothing (round 3, R7).

Under "Can flex" the main model pays for every hour of overtime, but it
also rewards an earlier finish ($200 per working day of makespan): a
part-timer working up to 25% over their hours can finish a day sooner for
less than that, so plans booked overtime that no date needed (pack CONTROL
rows: an 8 h package due on day 2 finished on day 1 with overtime).
Overtime is worth booking only when it keeps a date the project manager
set or lowers the cost. So when the solved plan books overtime, the model
is solved again with "Must hold" capacity (no overtime) and what the
overtime plan achieved as constraints:

* no later on any date: lateness against the go-live deadline and the
  target date (`model.target_of`) may not grow, nor against any phase
  milestone. Finishing earlier than every date set is not worth overtime;
* no dearer: labor cost at most the overtime plan's, its premium included;
* no worse skill fit: at most as many missing proficiency levels.

If such a plan exists, the overtime bought nothing and that plan, the best
one by the usual objective, replaces it. The re-solve may take as long as
the plan did, within what is left of the run's time limit, and at least
`MIN_SECONDS`; if it finds nothing in time the overtime plan stands.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ortools.sat.python import cp_model

from app.optimization.builder import ModelBuilder, Variables
from app.optimization.costing import overtime_hours, plan_cost
from app.optimization.model import Plan
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
        overtime_hours(problem.inp, plan, resource, problem.blocks) > 0
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
    for milestone in problem.inp.milestones:
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
        <= plan_cost(problem.inp, resources, plan, problem.blocks)
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
    constraints = replace(problem.inp.constraints, capacity_mode="hard")
    strict = prepare(replace(problem.inp, constraints=constraints))
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
