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
  (t,r) has its own duration d[t,r] and daily load q[t,r]. Someone who can
  give the task's daily demand (effort / WBS duration) works at the WBS
  duration; a part-timer who can't takes ceil(effort / their daily hours)
  working days, at most their daily hours per day. Contractor profiles keep
  the WBS duration. Task t has a start s_t shared by its candidates and an
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
  or reassigning. Bookings elsewhere make a task wait rather than stretch.
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
  `budget_total_cents` and `labor_cap_cents` both cap it (infra cost isn't
  modelled). Hard -> a constraint, soft ->
  `budget_penalty` per $1,000 over.
* Objective, in cents: labor cost + $200 per working day of makespan + soft
  penalties + $500 per task moved off its current owner (keeps
  recommendations few and explainable, plan.md §6.1) + $10 per working day a
  task starts after its baseline start (stops non-critical tasks drifting)
  + `target_penalty_per_day` per working day after the target date (only
  in the runs of remedies that try to keep it, `remedies.py`).

Infeasibility: the model is rebuilt without objective, with every hard
constraint group behind an assumption literal. A person's capacity group
holds their capacity limit (Must hold), the 125% cap (Can flex) and the
stretching of their tasks, so relaxing it lets them work at the WBS
durations: a part-timer who makes a date impossible is named.
`sufficient_assumptions_for_infeasibility()` gives a core, which is shrunk
by deletion (drop a group, re-solve, keep it dropped if still infeasible)
within the time budget. The reported list is
then usually minimal: each entry is needed for the conflict. If the budget
runs out it may contain extra groups.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from importlib.metadata import version

from ortools.sat.python import cp_model

from app.optimization import conflicts
from app.optimization import formatting as fmt
from app.optimization.builder import ModelBuilder
from app.optimization.model import (
    Conflict,
    Plan,
    PlanningInput,
    RunStatus,
    ScheduledTask,
    SolveOutcome,
)
from app.optimization.problem import Problem, prepare

logger = logging.getLogger("app.optimization")

SOLVER_VERSION: str = version("ortools")
SOLVER_NAME = f"OR-Tools CP-SAT {SOLVER_VERSION}"

_KIND_ORDER = ("deadline", "milestone", "budget", "labor", "skill", "capacity", "dependency")


@dataclass(frozen=True, slots=True)
class SolveOptions:
    time_limit_s: float = 20.0
    workers: int = 4
    # Extra time allowed for explaining an infeasible model (default: time_limit_s).
    iis_time_limit_s: float | None = None
    random_seed: int = 0


class SolveControl:
    """Lets another thread stop a running solve (used on server shutdown)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._solver: cp_model.CpSolver | None = None
        self._stopped = False

    @property
    def stopped(self) -> bool:
        return self._stopped

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._solver is not None:
                self._solver.stop_search()

    def attach(self, solver: cp_model.CpSolver) -> None:
        with self._lock:
            self._solver = solver
            if self._stopped:
                solver.stop_search()

    def detach(self) -> None:
        with self._lock:
            self._solver = None


# --------------------------------------------------------------------------- solving
def _solver(time_limit_s: float, workers: int, seed: int) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(time_limit_s, 0.01)
    solver.parameters.num_workers = max(workers, 1)
    solver.parameters.random_seed = seed
    solver.parameters.log_search_progress = False
    return solver


def _run(
    model: cp_model.CpModel, solver: cp_model.CpSolver, control: SolveControl | None
) -> cp_model.CpSolverStatus:
    if control is not None:
        control.attach(solver)
    try:
        return solver.solve(model)
    finally:
        if control is not None:
            control.detach()


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


def explain_infeasibility(
    problem: Problem, options: SolveOptions, control: SolveControl | None = None
) -> tuple[Conflict, ...]:
    """The hard constraint groups that conflict, headline constraints first."""
    builder = ModelBuilder(problem, explain=True)
    builder.build()
    model = builder.model
    if not builder.groups:
        return (conflicts.GENERIC,)
    literals = {literal.index: literal for literal, _ in builder.groups}
    texts = {literal.index: conflict for literal, conflict in builder.groups}
    # Report the headline constraints (deadline, budget, ...) before the details.
    order = {
        literal.index: (_KIND_ORDER.index(builder.kinds[literal.index]), position)
        for position, (literal, _) in enumerate(builder.groups)
    }
    budget = options.iis_time_limit_s or options.time_limit_s
    deadline = time.monotonic() + budget

    def infeasible(indices: set[int], limit: float) -> set[int] | None:
        """The (sub)core if the model is infeasible under these assumptions, else None."""
        model.clear_assumptions()
        model.add_assumptions([literals[index] for index in sorted(indices)])
        solver = _solver(limit, options.workers, options.random_seed)
        if _run(model, solver, control) != cp_model.INFEASIBLE:
            return None
        return (set(solver.sufficient_assumptions_for_infeasibility()) & indices) or indices

    core = infeasible(set(literals), budget / 2)
    if core is None:
        return (conflicts.GENERIC,)
    for index in sorted(core, key=order.__getitem__):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (control is not None and control.stopped):
            break
        if index not in core or len(core) == 1:
            continue
        smaller = infeasible(core - {index}, min(remaining, max(budget / 8, 0.05)))
        if smaller is not None:
            core = smaller
    return tuple(texts[index] for index in sorted(core, key=order.__getitem__))


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
    solver = _solver(options.time_limit_s, options.workers, options.random_seed)
    mapped = map_status(_run(builder.model, solver, control))

    if mapped in ("optimal", "feasible"):
        plan: dict[str, ScheduledTask] = dict(problem.done)
        for task_id, choices in variables.assign.items():
            chosen = next(
                resource_id
                for resource_id, assigned in choices.items()
                if solver.boolean_value(assigned)
            )
            plan[task_id] = ScheduledTask(
                task_id,
                solver.value(variables.start[task_id]),
                solver.value(variables.end[task_id]),
                chosen,
            )
        return outcome(
            mapped,
            plan=Plan(plan),
            objective=solver.objective_value,
            best_bound=solver.best_objective_bound,
            hit_time_limit=mapped == "feasible",
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
