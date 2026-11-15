"""Priced ways to keep the target date ("Your call"), or out of an infeasible run.

Purpose: Round-2 D2: when a run finishes after its target date or is infeasible, the worker
re-solves relaxed copies of the frozen input, each changing one thing the PM decides:
    * `specialist`: bring in a contractor (project rate or the $175/h estimate) for skills
      nobody has and any other late work package;
    * `overtime`: "Can flex" capacity, up to 25% over hours at 1.5x for employees;
    * `move_deadline`: accept the later date (for an infeasible run, go-live and hard
      milestones become soft);
    * `combined` (round 3, R6): "Change these together", only when no single copy gets an
      infeasible run a plan.
A copy is offered only if it produces a plan, uses what it adds and, for a slipping plan,
finishes earlier; `costCents` is the extra cost against this plan (the whole relaxed plan's cost
for an infeasible run).
How it fits: `price_remedies` is called by the optimizer job; `apply_remedy` by the API when a
run is created with `remedy`; remedy_options.py words and prices each option;
layer: optimization.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from app.optimization.model import PlanningInput, Task
from app.optimization.outcome import SolveOutcome
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


def allow_specialists(planning_input: PlanningInput) -> PlanningInput:
    """Contractors for skill gaps and for any late work, at the contractor rate or the
    $175/h estimate, to keep the target."""
    constraints = planning_input.constraints
    rate = constraints.contractor_rate_cents or DEFAULT_SPECIALIST_RATE_CENTS
    return replace(
        planning_input,
        constraints=replace(
            constraints,
            allow_contractors=True,
            contractor_rate_cents=rate,
            contractor_any_work=True,
            target_penalty_per_day=KEEP_TARGET_PENALTY_PER_DAY,
        ),
    )


def allow_overtime(planning_input: PlanningInput) -> PlanningInput:
    """People may work up to 25% over their hours ("Can flex"), to keep the target."""
    constraints = replace(
        planning_input.constraints,
        capacity_mode="soft",
        target_penalty_per_day=KEEP_TARGET_PENALTY_PER_DAY,
    )
    return replace(planning_input, constraints=constraints)


def move_deadline(planning_input: PlanningInput) -> PlanningInput:
    """Go-live and every phase milestone become soft: they may slip, at a penalty per day.

    The penalty keeps the solver finishing as early as it can, so the
    relaxed plan's completion date is the earliest date go-live can move to.
    """
    milestones = tuple(replace(milestone, mode="soft") for milestone in planning_input.milestones)
    constraints = replace(planning_input.constraints, deadline_mode="soft")
    return replace(planning_input, constraints=constraints, milestones=milestones)


def change_together(planning_input: PlanningInput) -> PlanningInput:
    """What every kind of blocker needs at once ("Change these together", R6).

    Specialists for the skills nobody on the team has, a go-live and milestones that
    may move (as little as possible: the target is kept where it can be) and a budget
    that may be exceeded, at a penalty far above any date so only when no plan fits.
    Everyone's hours stay as they are: once the dates may move, overtime only trades
    money for time, so it is never needed for a plan.
    """
    relaxed = move_deadline(planning_input)
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


def apply_remedy(planning_input: PlanningInput, remedy: RemedyId | None) -> PlanningInput:
    """The option a PM picked ("Use this option"), applied up front to one run."""
    if remedy == "specialist":
        return allow_specialists(planning_input)
    if remedy == "overtime":
        return allow_overtime(planning_input)
    if remedy == "move_deadline":
        return move_deadline(planning_input)
    if remedy == "combined":
        return change_together(planning_input)
    return planning_input


def variants(planning_input: PlanningInput, solved: bool) -> list[tuple[RemedyId, PlanningInput]]:
    """The relaxed inputs worth re-solving for a plan that slips (`solved`) or none."""
    constraints = planning_input.constraints
    found: list[tuple[RemedyId, PlanningInput]] = [
        ("specialist", allow_specialists(planning_input))
    ]
    # Under "Can flex" an infeasible run already had overtime: nothing to gain.
    if solved or constraints.capacity_mode == "hard":
        found.append(("overtime", allow_overtime(planning_input)))
    hard_deadline = constraints.must_finish_by is not None and constraints.deadline_mode == "hard"
    hard_milestone = any(milestone.mode == "hard" for milestone in planning_input.milestones)
    if not solved and (hard_deadline or hard_milestone):
        found.append(("move_deadline", move_deadline(planning_input)))
    return found


def price_remedies(
    planning_input: PlanningInput,
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
    found = variants(planning_input, solved)
    if not solved:
        # Tried last, and only when no single option gets a plan (R6).
        found.append(("combined", change_together(planning_input)))
    # The run's time limit is shared by the variants, with a floor so each one can solve.
    share = SolveOptions(
        time_limit_s=max(options.time_limit_s / len(found), MIN_VARIANT_SECONDS),
        workers=options.workers,
        random_seed=options.random_seed,
    )
    base_finish = finish_of(planning_input, current) if current is not None and solved else None
    base_cost = cost_of(planning_input, current) if current is not None and solved else 0
    remedies: list[Remedy] = []
    for remedy_id, variant in found:
        if (control is not None and control.stopped) or (remedy_id == "combined" and remedies):
            break
        outcome = solve(variant, share, control, explain=False)
        if outcome.plan is None:
            continue
        # Next to a plan, an option is only worth offering when it finishes earlier.
        if base_finish is not None and finish_of(variant, outcome) >= base_finish:
            continue
        # Priced as the extra cost over this plan; without a plan, the whole relaxed plan's.
        remedy = DESCRIBE[remedy_id](
            planning_input, variant, outcome, cost_of(variant, outcome) - base_cost
        )
        if remedy is not None:
            remedies.append(remedy)
    if current is not None and solved:
        remedies.append(accept_remedy(planning_input, current))
    return remedies
