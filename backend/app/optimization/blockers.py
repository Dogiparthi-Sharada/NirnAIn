"""Every independent blocker of an infeasible plan, each in plain English (round 3, R6).

The explanation model (`builder.py`, `explain=True`) has no objective and
puts every hard constraint group (the deadline, a milestone, the budget, a
work package's required skill, a person's capacity, a dependency) behind an
assumption literal; `conflicts.py` words each group. `explain_infeasibility`
repeats, at most `MAX_BLOCKERS` times and within the time budget:

1. Find a core: groups that can't all hold, from CP-SAT's
   `sufficient_assumptions_for_infeasibility()`, shrunk by deletion (drop a
   group, re-solve, keep it dropped if still infeasible). Each group in it
   is then usually needed; if the time runs out it may keep extra groups.
2. Relax the core's headline group (the first in `KIND_ORDER`: the deadline
   before the budget before a skill before a person's capacity ...) for the
   rest of the search, as if the project manager had changed it.
3. Stop as soon as the model is feasible: no blocker is left.

Each round may use half of the time left (the last one all of it), so a
slow first core can't starve the others. All cores are reported together,
headline constraints first: a project that misses two skills and can't make
its deadline names all three in one run, not one per run.
"""

from __future__ import annotations

import time

from ortools.sat.python import cp_model

from app.optimization import conflicts
from app.optimization.builder import ModelBuilder
from app.optimization.model import Conflict
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
        assumed.discard(min(core, key=search.rank.__getitem__))
    if not found:
        return (conflicts.GENERIC,)
    return tuple(search.texts[index] for index in sorted(found, key=search.rank.__getitem__))
