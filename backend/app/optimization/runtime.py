"""How CP-SAT runs: the solve options, stopping a solve, and the settings a run records.

`solver.solve` (the plan), `blockers.explain_infeasibility` (why there is
none) and `remedies.price_remedies` (the ways out) all run CP-SAT through
`new_solver` and `run_model`, so one `SolveControl` can stop any of them on
server shutdown. `solver_settings` is what a run stores in
`options.solverSettings` (round 3, R7): the solver and its version, the
time limit, workers and seed, and the objective weights in cents, so a run
can be reproduced and compared.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from ortools.sat.python import cp_model

from app.optimization import builder
from app.optimization.model import Constraints

SOLVER_VERSION: str = version("ortools")
SOLVER_NAME = f"OR-Tools CP-SAT {SOLVER_VERSION}"


@dataclass(frozen=True, slots=True)
class SolveOptions:
    """The time limit and workers of one solve (`OPTIMIZER_TIME_LIMIT_S`, `OPTIMIZER_WORKERS`)."""

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
        """Whether `stop` was called: no further solve should start."""
        return self._stopped

    def stop(self) -> None:
        """Stop the running solve, and every later one this control attaches to."""
        with self._lock:
            self._stopped = True
            if self._solver is not None:
                self._solver.stop_search()

    def attach(self, solver: cp_model.CpSolver) -> None:
        """Track the solver about to run (stopped at once if `stop` came first)."""
        with self._lock:
            self._solver = solver
            if self._stopped:
                solver.stop_search()

    def detach(self) -> None:
        """Forget the solver that just finished."""
        with self._lock:
            self._solver = None


def new_solver(time_limit_s: float, workers: int, seed: int) -> cp_model.CpSolver:
    """A quiet CP-SAT solver with this time limit, workers and seed."""
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(time_limit_s, 0.01)
    solver.parameters.num_workers = max(workers, 1)
    solver.parameters.random_seed = seed
    solver.parameters.log_search_progress = False
    return solver


def run_model(
    model: cp_model.CpModel, solver: cp_model.CpSolver, control: SolveControl | None
) -> cp_model.CpSolverStatus:
    """Solve `model`, stoppable through `control`."""
    if control is not None:
        control.attach(solver)
    try:
        return solver.solve(model)
    finally:
        if control is not None:
            control.detach()


def solver_settings(options: SolveOptions, constraints: Constraints) -> dict[str, Any]:
    """`options.solverSettings`: the solver, how it runs, and the run's objective weights."""
    return {
        "solver": "OR-Tools CP-SAT",
        "version": SOLVER_VERSION,
        "timeLimitS": options.time_limit_s,
        "workers": options.workers,
        "seed": options.random_seed,
        "weights": {
            "makespanPerDayCents": builder.MAKESPAN_CENTS_PER_DAY,
            "startDriftPerDayCents": builder.SHIFT_CENTS_PER_DAY,
            "ownerChangeCents": builder.CHANGE_OWNER_CENTS,
            "skillGapPerLevelCents": builder.SKILL_GAP_CENTS_PER_LEVEL,
            "lateDayCents": round(constraints.deadline_penalty_per_day * 100),
            "overBudgetPer1000Cents": round(constraints.budget_penalty * 100),
            "overtimePerHourCents": round(constraints.capacity_penalty * 100),
            "pastTargetPerDayCents": round(constraints.target_penalty_per_day * 100),
        },
    }
