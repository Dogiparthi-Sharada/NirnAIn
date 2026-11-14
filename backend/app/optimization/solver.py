"""CP-SAT model for resource-constrained scheduling with skill-based assignment.

Formulation (day granularity; plan.md §6.1 has the MILP intent it follows).
`problem.py` pre-computes the numbers, `builder.py` adds the variables and
constraints below (each person's modes and capacity in `workload.py`),
`conflicts.py` words the infeasibility explanations.

* Time: working-day indices from the project start, 0..H, H = R + Σ
  (longest candidate duration of each task) + Σ |lags| + the span of
  existing allocations + one week, which always admits a serial schedule.
  R is today's index (`baseline.planning_start`): unfinished work starts at
  s_t >= R. Finished work (0 days left) is not modelled; it keeps the dates
  of the plan in force and its links are already met.
* Assignment: one boolean x[t,r] per candidate resource, exactly one true.
* Duration follows the person (multi-mode, `load.task_mode`): candidate
  (t,r) has its own duration d[t,r] and daily load q[t,r]. Their skill
  level sets their pace (`speed.py`, D5): each level above the task's
  minimum is 10% faster (at most 30%), each level below 25% slower; the
  effort and the full-time duration scale with it, and so does the cost.
  Someone who can give the daily demand (effort / that duration) works at
  that duration; a part-timer who can't takes ceil(effort / their daily
  hours) working days, at most their daily hours per day. Contractor
  profiles keep the WBS duration. Task t has a start s_t shared by its candidates and an
  end e_t, set by the chosen candidate's mode. Each employee mode carries an
  optional interval from s_t. So cutting someone's hours stretches their
  tasks and lengthens the plan, and giving hours back shortens it.
* Capacity: per employee, a cumulative constraint over those intervals with
  their daily loads, plus fixed intervals for hours already allocated to
  other projects, against daily capacity = weekly hours / working days.
  Stretched tasks fit exactly, so only overlapping work can exceed it.
  "Must hold" (hard) forbids that. "Can flex" (soft) lets each mode move
  part of its daily load, o, into overtime and pays `capacity_penalty`
  plus the overtime premium (`costing.py`: 1.5x an employee's rate,
  nothing extra for a contractor) per hour of overtime on every day it runs
  (o · d): the over-allocated hours, overestimated only when tasks partly
  overlap. A part-timer may also take a "flex" mode, stretched at 25% over
  their hours. The full load never exceeds 125% of capacity (round-2 D2).
  Overtime is then chosen only when it saves more than stretching, waiting
  or reassigning, and kept only when it keeps a date or saves money: a
  plan whose overtime buys neither is solved again without it (R7,
  `polish.py`). Bookings elsewhere make a task wait rather than stretch.
  Contractor profiles have no capacity limit: they stand for as many
  people as needed.
* Skills: in hard mode only qualified resources are candidates; in soft mode
  everyone is, at a penalty per missing proficiency level.
* Dependencies FS/SS/FF/SF with lag in working days, on the real starts and
  ends: FS s_succ >= e_pred + lag, SS s_succ >= s_pred + lag, FF e_succ >=
  e_pred + lag, SF e_succ >= s_pred + lag.
* Deadline / milestones: hard -> e_t <= D; soft -> tardiness in working days
  times `deadline_penalty_per_day`. A milestone on a summary node covers its
  leaf tasks; milestones without a WBS node are informational.
* Budget: labor cost = Σ x[t,r] · hours_t · rate_r (the effort, so a
  stretched task costs the same). Employees are costed at their company
  rate, else the standard $85/h; team contractors at their own rate, else
  the project's contractor rate (`resources.planned_rate_cents`). The overtime
  premium is in the objective and the reported cost, not in this cap.
  `budget_total_cents` less its contingency reserve (`contingency_pct`,
  held back: cost + reserve <= budget) and `labor_cap_cents` both cap it
  (infra cost isn't modelled). Hard -> a constraint, soft ->
  `budget_penalty` per $1,000 over the budget less the reserve.
* Objective, in cents: labor cost + $200 per working day of makespan + soft
  penalties + $500 per task moved off its current owner (keeps
  recommendations few and explainable, plan.md §6.1) + $10 per working day a
  task starts after its baseline start (stops non-critical tasks drifting)
  + `target_penalty_per_day` per working day after the target date (only
  in the runs of remedies that try to keep it, `remedies.py`).

Infeasibility (`blockers.py`): the model is rebuilt without objective, with
every hard constraint group behind an assumption literal. A person's
capacity group holds their capacity limit (Must hold), the 125% cap (Can
flex) and the stretching of their tasks, so relaxing it lets them work at
the WBS durations: a part-timer who makes a date impossible is named. Each
core (`sufficient_assumptions_for_infeasibility()`, shrunk by deletion) is
one blocker; its headline group is relaxed and the search repeats, so every
independent blocker is named in one run (round 3, R6). Each entry is then
usually needed for its conflict; if the time budget runs out a core may
contain extra groups.
"""

from __future__ import annotations

import logging
import time

from ortools.sat.python import cp_model

from app.optimization import formatting as fmt
from app.optimization.blockers import explain_infeasibility
from app.optimization.builder import ModelBuilder
from app.optimization.model import PlanningInput, RunStatus, SolveOutcome
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
    inp: PlanningInput,
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
    problem = prepare(inp)

    def outcome(status: RunStatus, **kwargs: object) -> SolveOutcome:
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
        if inp.constraints.capacity_mode == "soft" and books_overtime(problem, plan):
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
