"""Priced ways to keep the target date ("Your call"), or out of an infeasible run.

Round-2 decision D2. When a solved run finishes after its target date
(`model.target_of`: the plan in force's completion for a re-plan, else the
must-finish-by date), or the run is infeasible, the worker re-solves
relaxed copies of the frozen input. Each copy changes one thing the
project manager decides:

* `specialist`: bring in a contractor, at the project's contractor rate or
  the $175/h estimate: a specialist for skills nobody on the team has
  (D6), and a contractor who may take any other late work package,
  skill-agnostic (`Constraints.contractor_any_work`).
* `overtime`: "Can flex" capacity: the people on the late work may work up
  to 25% over their hours, billed at 1.5x an employee's cost and at a
  contractor's own rate.
* `move_deadline`: accept the later date. For a plan that slips this is
  the plan itself (no re-solve, no extra cost); for an infeasible run,
  go-live and hard phase milestones become soft.

* `combined` (round 3, R6): "Change these together", only for an
  infeasible run that no single copy gets a plan. What every kind of
  blocker (`blockers.py`) needs at once: specialists for missing skills, a
  movable go-live and milestones, and a budget exceeded only when nothing
  else fits (`change_together`). Its `changes` list the ones the relaxed
  plan needs, e.g. a contractor for two missing skills and a later go-live.

The specialist, overtime and combined copies try hard to keep the target
(`target_penalty_per_day`), so they reach the earliest date their
relaxation allows. A copy is offered only if it produces a plan, actually
uses what it adds (contractor or overtime hours) and, for a plan that
slips, finishes earlier than it. `costCents` is the extra cost against
this plan; an infeasible run has no plan, so its remedies carry the whole
relaxed plan's cost. Variants skip the infeasibility explanation and share
the run's time limit. A run created with `remedy` applies the same copy
up front (`apply_remedy`). `remedy_options.py` words and prices each option.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from app.optimization.model import PlanningInput, SolveOutcome, Task
from app.optimization.remedy_options import (
    DEFAULT_SPECIALIST_RATE_CENTS,
    DESCRIBE,
    Remedy,
    RemedyId,
    accept_remedy,
    cost_of,
    finish_of,
)
from app.optimization.solver import SolveControl, SolveOptions, solve

__all__ = [
    "DEFAULT_SPECIALIST_RATE_CENTS",
    "KEEP_TARGET_PENALTY_PER_DAY",
    "Remedy",
    "RemedyId",
    "allow_overtime",
    "allow_specialists",
    "apply_remedy",
    "change_together",
    "covers",
    "move_deadline",
    "price_remedies",
    "variants",
]

MIN_VARIANT_SECONDS = 1.0
# A remedy's plan pays this per working day after the target, far above any
# labor saving, so it keeps the date when its relaxation can (dollars).
KEEP_TARGET_PENALTY_PER_DAY = 5_000.0
# The combined option's budget penalty per $1,000 over (dollars): it only goes over
# the budget when nothing else fits.
UNAVOIDABLE_BUDGET_PENALTY = 100_000.0


# --------------------------------------------------------------------------- relaxations
def covers(skills: Mapping[str, int], task: Task) -> bool:
    """Whether a skill profile (skill id -> proficiency) meets every requirement of `task`."""
    return all(
        skills.get(requirement.skill_id, 0) >= requirement.min_proficiency
        for requirement in task.skills
    )


def allow_specialists(inp: PlanningInput) -> PlanningInput:
    """Contractors for skill gaps and for any late work, at the contractor rate or the
    $175/h estimate, to keep the target."""
    constraints = inp.constraints
    rate = constraints.contractor_rate_cents or DEFAULT_SPECIALIST_RATE_CENTS
    return replace(
        inp,
        constraints=replace(
            constraints,
            allow_contractors=True,
            contractor_rate_cents=rate,
            contractor_any_work=True,
            target_penalty_per_day=KEEP_TARGET_PENALTY_PER_DAY,
        ),
    )


def allow_overtime(inp: PlanningInput) -> PlanningInput:
    """People may work up to 25% over their hours ("Can flex"), to keep the target."""
    constraints = replace(
        inp.constraints,
        capacity_mode="soft",
        target_penalty_per_day=KEEP_TARGET_PENALTY_PER_DAY,
    )
    return replace(inp, constraints=constraints)


def move_deadline(inp: PlanningInput) -> PlanningInput:
    """Go-live and every phase milestone become soft: they may slip, at a penalty per day.

    The penalty keeps the solver finishing as early as it can, so the
    relaxed plan's completion date is the earliest date go-live can move to.
    """
    milestones = tuple(replace(milestone, mode="soft") for milestone in inp.milestones)
    constraints = replace(inp.constraints, deadline_mode="soft")
    return replace(inp, constraints=constraints, milestones=milestones)


def change_together(inp: PlanningInput) -> PlanningInput:
    """What every kind of blocker needs at once ("Change these together", R6).

    Specialists for the skills nobody on the team has, a go-live and milestones that
    may move (as little as possible: the target is kept where it can be) and a budget
    that may be exceeded, at a penalty far above any date so only when no plan fits.
    Everyone's hours stay as they are: once the dates may move, overtime only trades
    money for time, so it is never needed for a plan.
    """
    relaxed = move_deadline(inp)
    constraints = relaxed.constraints
    return replace(
        relaxed,
        constraints=replace(
            constraints,
            allow_contractors=True,
            contractor_rate_cents=constraints.contractor_rate_cents
            or DEFAULT_SPECIALIST_RATE_CENTS,
            budget_mode="soft",
            budget_penalty=max(constraints.budget_penalty, UNAVOIDABLE_BUDGET_PENALTY),
            target_penalty_per_day=KEEP_TARGET_PENALTY_PER_DAY,
        ),
    )


def apply_remedy(inp: PlanningInput, remedy: RemedyId | None) -> PlanningInput:
    """The option a PM picked ("Use this option"), applied up front to one run."""
    if remedy == "specialist":
        return allow_specialists(inp)
    if remedy == "overtime":
        return allow_overtime(inp)
    if remedy == "move_deadline":
        return move_deadline(inp)
    if remedy == "combined":
        return change_together(inp)
    return inp


def variants(inp: PlanningInput, solved: bool) -> list[tuple[RemedyId, PlanningInput]]:
    """The relaxed inputs worth re-solving for a plan that slips (`solved`) or none."""
    constraints = inp.constraints
    found: list[tuple[RemedyId, PlanningInput]] = [("specialist", allow_specialists(inp))]
    # Under "Can flex" an infeasible run already had overtime: nothing to gain.
    if solved or constraints.capacity_mode == "hard":
        found.append(("overtime", allow_overtime(inp)))
    hard_deadline = constraints.must_finish_by is not None and constraints.deadline_mode == "hard"
    hard_milestone = any(milestone.mode == "hard" for milestone in inp.milestones)
    if not solved and (hard_deadline or hard_milestone):
        found.append(("move_deadline", move_deadline(inp)))
    return found


def price_remedies(
    inp: PlanningInput,
    options: SolveOptions,
    control: SolveControl | None = None,
    current: SolveOutcome | None = None,
) -> list[Remedy]:
    """Re-solve each relaxed variant; return the ones worth offering. CPU-bound.

    `current` is this run's outcome: with a plan, the options must finish
    earlier than it and are priced against it, and accepting its date is
    always the last option.
    """
    solved = current is not None and current.plan is not None
    found = variants(inp, solved)
    if not solved:
        # Tried last, and only when no single option gets a plan (R6).
        found.append(("combined", change_together(inp)))
    share = SolveOptions(
        time_limit_s=max(options.time_limit_s / len(found), MIN_VARIANT_SECONDS),
        workers=options.workers,
        random_seed=options.random_seed,
    )
    base_finish = finish_of(inp, current) if current is not None and solved else None
    base_cost = cost_of(inp, current) if current is not None and solved else 0
    remedies: list[Remedy] = []
    for remedy_id, variant in found:
        if (control is not None and control.stopped) or (remedy_id == "combined" and remedies):
            break
        outcome = solve(variant, share, control, explain=False)
        if outcome.plan is None:
            continue
        if base_finish is not None and finish_of(variant, outcome) >= base_finish:
            continue
        remedy = DESCRIBE[remedy_id](inp, variant, outcome, cost_of(variant, outcome) - base_cost)
        if remedy is not None:
            remedies.append(remedy)
    if current is not None and solved:
        remedies.append(accept_remedy(inp, current))
    return remedies
