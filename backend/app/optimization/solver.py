"""CP-SAT model for resource-constrained scheduling with skill-based assignment.

Purpose: Find the cheapest plan that respects the PM's hard constraints. Formulation (day
granularity; plan.md §6.1 has the MILP intent it follows):
    * Time: working-day indices 0..H from the project start; H admits a serial schedule.
      Unfinished work starts at s_t >= R (today's index); finished work keeps its dates.
    * Assignment: one boolean x[t,r] per candidate resource, exactly one true.
    * Duration follows the person (multi-mode, `load.task_mode`): skill level sets the pace
      (D5: +10% per level above the minimum, at most 30%; -25% per level below) and a
      part-timer takes ceil(effort / daily hours) days. Each employee mode is an optional
      interval from s_t; the task's end e_t is set by the chosen mode.
    * Capacity: per employee a cumulative over those intervals plus other projects' bookings
      against weekly hours / working days. "Must hold" forbids overtime; "Can flex" pays
      `capacity_penalty` plus the overtime premium per overtime hour, never above 125% (D2),
      and overtime that keeps no date and saves no money is solved away (R7, `polish.py`).
    * Skills: hard mode keeps only qualified candidates; soft mode charges per missing level.
    * Dependencies FS/SS/FF/SF with lag on the real starts and ends.
    * Deadline / milestones: hard -> e_t <= D; soft -> working days late x penalty.
    * Budget: labor cost = sum of x[t,r] x hours_t x rate_r, capped by the budget less its
      contingency reserve and by the labor cap (hard), or `budget_penalty` per $1,000 over.
    * Objective (cents): labor cost + $200 per day of makespan + soft penalties + $500 per task
      moved off its owner + $10 per day a task starts after its baseline start
      + `target_penalty_per_day` after the target date (remedy runs only).
Infeasibility (`blockers.py`): the model is rebuilt without objective, every hard constraint
group behind an assumption literal, and each core found is one blocker (round 3, R6).
How it fits: `solve()` is called by the optimizer job (off the event loop) and by remedy
pricing; problem.py pre-computes the numbers, builder.py and workload.py add the variables and
constraints, conflicts.py words the explanations; layer: optimization.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import logging
import time

from ortools.sat.python import cp_model

from app.optimization import formatting as fmt
from app.optimization.blockers import explain_infeasibility
from app.optimization.builder import ModelBuilder
from app.optimization.model import PlanningInput
from app.optimization.outcome import RunStatus, SolveOutcome
from app.optimization.polish import books_overtime, without_overtime
from app.optimization.problem import prepare
from app.optimization.runtime import (
    SOLVER_NAME,
    SOLVER_VERSION,
    SolveControl,
    SolveOptions,
    new_solver,
    run_model,
)

logger = logging.getLogger("app.optimization")

__all__ = ["SOLVER_NAME", "SOLVER_VERSION", "SolveControl", "SolveOptions", "map_status", "solve"]


def map_status(status: cp_model.CpSolverStatus) -> RunStatus:
    """CP-SAT status -> `solver_status`. UNKNOWN means the time limit hit first."""
    if status == cp_model.OPTIMAL:
        return "optimal"
    if status == cp_model.FEASIBLE:
        return "feasible"
    if status == cp_model.INFEASIBLE:
        return "infeasible"
    if status == cp_model.UNKNOWN:
        return "timeout"
    return "failed"


def solve(
    planning_input: PlanningInput,
    options: SolveOptions | None = None,
    control: SolveControl | None = None,
    *,
    explain: bool = True,
) -> SolveOutcome:
    """Solve the planning problem. CPU-bound: call it off the event loop.

    `explain=False` skips the infeasibility explanation (used when pricing
    remedies, where only "is there a plan, and what is it" matters).
    """
    options = options or SolveOptions()
    began = time.perf_counter()
    problem = prepare(planning_input)

    def outcome(status: RunStatus, **kwargs: object) -> SolveOutcome:
        """Return a `SolveOutcome` with solver identity, timing, resources and baseline."""
        return SolveOutcome(
            status=status,
            solver=SOLVER_NAME,
            solver_version=SOLVER_VERSION,
            wall_seconds=time.perf_counter() - began,
            resources=problem.resources,
            baseline=problem.baseline,
            **kwargs,  # type: ignore[arg-type]
        )

    builder = ModelBuilder(problem, explain=False)
    variables = builder.build()
    solver = new_solver(options.time_limit_s, options.workers, options.random_seed)
    mapped = map_status(run_model(builder.model, solver, control))

    if mapped in ("optimal", "feasible"):
        plan = variables.read(solver, problem.done)
        objective, bound = solver.objective_value, solver.best_objective_bound
        optimal = mapped == "optimal"
        # Overtime that keeps no date and saves no money is dropped (R7, `polish.py`).
        if planning_input.constraints.capacity_mode == "soft" and books_overtime(problem, plan):
            spent = time.perf_counter() - began
            # As long again as the plan took, within the run's time limit (at least 1 s).
            budget = min(spent, options.time_limit_s - spent)
            polished = without_overtime(problem, plan, options, control, budget)
            if polished is not None:
                plan, objective, bound = polished.plan, polished.objective, polished.best_bound
                optimal = optimal and polished.optimal
        return outcome(
            "optimal" if optimal else "feasible",
            plan=plan,
            objective=objective,
            best_bound=bound,
            hit_time_limit=not optimal,
        )
    if mapped == "infeasible":
        found = explain_infeasibility(problem, options, control) if explain else ()
        return outcome("infeasible", conflicts=found)
    if mapped == "timeout":
        return outcome(
            "timeout",
            hit_time_limit=True,
            message=(
                f"No feasible plan was found within "
                f"{fmt.plural(round(options.time_limit_s), 'second')}. "
                "Try again, or relax some hard constraints."
            ),
        )
    logger.error("cp-sat model invalid", extra={"validation": builder.model.validate()[:500]})
    return outcome("failed", message="The optimizer couldn't build a valid model for this project.")
