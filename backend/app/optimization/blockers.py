"""Every independent blocker of an infeasible plan, each in plain English (round 3, R6).

Purpose: A PM must learn everything that makes the plan impossible in one run, not one blocker
per run. The explanation model (`builder.py`, `explain=True`) puts every hard constraint group
(deadline, milestone, budget, a package's required skill, a person's capacity, a dependency)
behind an assumption literal; `explain_infeasibility` repeats, at most `MAX_BLOCKERS` times:
    1. find a core: groups that can't all hold, from CP-SAT's
       `sufficient_assumptions_for_infeasibility()`, shrunk by deletion;
    2. relax the core's headline group (first in `KIND_ORDER`) for the rest of the search,
       as if the PM had changed it;
    3. stop as soon as the model is feasible: no blocker is left.
Each round may use half of the time left (the last one all of it), so a slow first core can't
starve the others; all cores are reported together, headline constraints first.
How it fits: Called by `solver.solve` for an infeasible run; texts come from conflicts.py;
layer: optimization.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import time

from ortools.sat.python import cp_model

from app.optimization import conflicts
from app.optimization.builder import ModelBuilder
from app.optimization.outcome import Conflict
from app.optimization.problem import Problem
from app.optimization.runtime import SolveControl, SolveOptions, new_solver, run_model

KIND_ORDER = ("deadline", "milestone", "budget", "labor", "skill", "capacity", "dependency")
MAX_BLOCKERS = 5
MIN_SEARCH_SECONDS = 0.05


class _Search:
    """The explanation model of one problem, solved under chosen assumptions."""

    def __init__(
        self, problem: Problem, options: SolveOptions, control: SolveControl | None
    ) -> None:
        """Build the explanation model once and index its assumption literals."""
        builder = ModelBuilder(problem, explain=True)
        builder.build()
        self.model = builder.model
        self.options = options
        self.control = control
        self.literals = {literal.index: literal for literal, _ in builder.groups}
        self.texts = {literal.index: conflict for literal, conflict in builder.groups}
        # Headline constraints (deadline, budget, ...) before the details.
        self.rank = {
            literal.index: (KIND_ORDER.index(builder.kinds[literal.index]), position)
            for position, (literal, _) in enumerate(builder.groups)
        }

    @property
    def stopped(self) -> bool:
        """Whether the server is shutting down: no further search should start."""
        return self.control is not None and self.control.stopped

    def core(self, assumed: set[int], limit: float) -> set[int] | None:
        """A core within `assumed` if they can't all hold, else None (feasible or no time)."""
        self.model.clear_assumptions()
        # Assuming a group's literal switches its constraints on; on infeasibility CP-SAT names
        # a subset of the assumptions it needed, which is the core.
        self.model.add_assumptions([self.literals[index] for index in sorted(assumed)])
        solver = new_solver(limit, self.options.workers, self.options.random_seed)
        if run_model(self.model, solver, self.control) != cp_model.INFEASIBLE:
            return None
        return (set(solver.sufficient_assumptions_for_infeasibility()) & assumed) or assumed

    def shrink(self, core: set[int], until: float, step: float) -> set[int]:
        """Drop every group the core doesn't need (a deletion filter), until `until`."""
        for index in sorted(core, key=self.rank.__getitem__):
            remaining = until - time.monotonic()
            if remaining <= 0 or self.stopped:
                break
            if index not in core or len(core) == 1:
                continue
            smaller = self.core(core - {index}, min(remaining, step))
            if smaller is not None:
                core = smaller
        return core


def explain_infeasibility(
    problem: Problem, options: SolveOptions, control: SolveControl | None = None
) -> tuple[Conflict, ...]:
    """The hard constraint groups of every independent conflict, headline constraints first."""
    search = _Search(problem, options, control)
    if not search.literals:
        return (conflicts.GENERIC,)
    budget = options.iis_time_limit_s or options.time_limit_s
    until = time.monotonic() + budget
    step = max(budget / 8, MIN_SEARCH_SECONDS)
    assumed = set(search.literals)
    found: set[int] = set()
    for round_number in range(MAX_BLOCKERS):
        remaining = until - time.monotonic()
        if remaining < MIN_SEARCH_SECONDS or search.stopped:
            break
        share = remaining if round_number == MAX_BLOCKERS - 1 else remaining / 2
        round_end = time.monotonic() + share
        core = search.core(assumed, share)
        if core is None:
            break  # feasible without the relaxed groups: every blocker is named
        core = search.shrink(core, round_end, step)
        found |= core
        # Relax the core's headline group, as if the PM had changed it, to find the next one.
        assumed.discard(min(core, key=search.rank.__getitem__))
    if not found:
        return (conflicts.GENERIC,)
    return tuple(search.texts[index] for index in sorted(found, key=search.rank.__getitem__))
