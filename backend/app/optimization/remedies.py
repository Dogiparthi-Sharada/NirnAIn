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

The specialist and overtime copies try hard to keep the target
(`target_penalty_per_day`), so they reach the earliest date their
relaxation allows. A copy is offered only if it produces a plan, actually
uses what it adds (contractor or overtime hours) and, for a plan that
slips, finishes earlier than it. `costCents` is the extra cost against
this plan; an infeasible run has no plan, so its remedies carry the whole
relaxed plan's cost. Variants skip the infeasibility explanation and share
the run's time limit. A run created with `remedy` applies the same copy
up front (`apply_remedy`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal

from app.optimization import formatting as fmt
from app.optimization.calendar import workday_date, workdays_late
from app.optimization.costing import overtime_hours, plan_cost
from app.optimization.load import allocation_blocks
from app.optimization.model import PlanningInput, Resource, SolveOutcome, Task, target_of
from app.optimization.resources import CONTRACTOR_ID
from app.optimization.solver import SolveControl, SolveOptions, solve

DEFAULT_SPECIALIST_RATE_CENTS = 17_500  # $175/h until the PM confirms a rate
MIN_VARIANT_SECONDS = 1.0
# A remedy's plan pays this per working day after the target, far above any
# labor saving, so it keeps the date when its relaxation can (dollars).
KEEP_TARGET_PENALTY_PER_DAY = 5_000.0

RemedyId = Literal["specialist", "overtime", "move_deadline"]


@dataclass(frozen=True, slots=True)
class Remedy:
    """One priced option; `cost_cents` is extra against this plan (whole plan if none)."""

    id: RemedyId
    title: str
    detail: str
    cost_cents: int | None
    completion_date: date | None

    def to_json(self) -> dict[str, Any]:
        """The camelCase shape stored in `result.remedies`."""
        return {
            "id": self.id,
            "title": self.title,
            "detail": self.detail,
            "costCents": self.cost_cents,
            "completionDate": self.completion_date.isoformat() if self.completion_date else None,
        }


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


def apply_remedy(inp: PlanningInput, remedy: RemedyId | None) -> PlanningInput:
    """The option a PM picked ("Use this option"), applied up front to one run."""
    if remedy == "specialist":
        return allow_specialists(inp)
    if remedy == "overtime":
        return allow_overtime(inp)
    if remedy == "move_deadline":
        return move_deadline(inp)
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


# --------------------------------------------------------------------------- pricing
def _finish(inp: PlanningInput, outcome: SolveOutcome) -> date:
    assert outcome.plan is not None  # noqa: S101 - callers check
    per_week = inp.constraints.working_days_per_week
    return workday_date(inp.start_date, max(outcome.plan.makespan - 1, 0), per_week)


def _contractor_hours(inp: PlanningInput, outcome: SolveOutcome) -> list[tuple[Resource, float]]:
    """Each contractor profile the plan uses, with the hours of work it takes."""
    assert outcome.plan is not None  # noqa: S101 - callers check
    tasks = {task.id: task for task in inp.tasks}
    hours: dict[str, float] = {}
    for scheduled in outcome.plan.tasks.values():
        if scheduled.resource_id is not None:
            hours[scheduled.resource_id] = (
                hours.get(scheduled.resource_id, 0.0) + tasks[scheduled.task_id].hours
            )
    return [
        (resource, hours[resource.id])
        for resource in outcome.resources
        if resource.kind == "contractor" and hours.get(resource.id, 0.0) > 0
    ]


def _cost(inp: PlanningInput, outcome: SolveOutcome) -> int:
    """The plan's labor cost, overtime premium included."""
    assert outcome.plan is not None  # noqa: S101 - callers check
    resources = {resource.id: resource for resource in outcome.resources}
    return plan_cost(inp, resources, outcome.plan, allocation_blocks(inp))


def _overtime(inp: PlanningInput, outcome: SolveOutcome) -> dict[str, float]:
    """Overtime hours by person's name, for those who work any."""
    assert outcome.plan is not None  # noqa: S101 - callers check
    blocks = allocation_blocks(inp)
    hours = {
        resource.name: overtime_hours(inp, outcome.plan, resource, blocks)
        for resource in outcome.resources
    }
    return {name: amount for name, amount in hours.items() if amount > 0}


def _versus(inp: PlanningInput, finish: date) -> str:
    """' (3 working days after Oct 30)' against the target date, or ''."""
    target = target_of(inp)
    if target is None:
        return ""
    per_week = inp.constraints.working_days_per_week
    after = workdays_late(inp.start_date, target, finish, per_week)
    before = workdays_late(inp.start_date, finish, target, per_week)
    if after:
        return f" ({fmt.plural(after, 'working day')} after {fmt.short_day(target)})"
    if before:
        return f" ({fmt.plural(before, 'working day')} before {fmt.short_day(target)})"
    return f" (on the {fmt.short_day(target)} target)"


def _specialist_remedy(
    inp: PlanningInput, variant: PlanningInput, outcome: SolveOutcome, extra: int
) -> Remedy | None:
    used = _contractor_hours(variant, outcome)
    if not used:
        return None  # it didn't need a contractor: not this option's date
    finish = _finish(variant, outcome)
    rate = variant.constraints.contractor_rate_cents or DEFAULT_SPECIALIST_RATE_CENTS
    estimate = " (estimate)" if inp.constraints.contractor_rate_cents is None else ""
    # Specialist profiles are named "Contractor: SAP specialist" (`resources.py`).
    gaps = [
        profile.name.removeprefix("Contractor: ").removesuffix(" specialist")
        for profile, _ in used
        if profile.id != CONTRACTOR_ID
    ]
    covering = f", covering {' and '.join(gaps)}, which nobody on the team has" if gaps else ""
    detail = (
        f"A contractor takes {sum(hours for _, hours in used):g} hours of the work at "
        f"${rate / 100:,.0f}/h{estimate}{covering}"
        f" · go-live {fmt.day(finish)}{_versus(inp, finish)}"
    )
    return Remedy("specialist", "Bring in a contractor", detail, extra, finish)


def _overtime_remedy(
    inp: PlanningInput, variant: PlanningInput, outcome: SolveOutcome, extra: int
) -> Remedy | None:
    overtime = _overtime(variant, outcome)
    if not overtime:
        return None
    finish = _finish(variant, outcome)
    names = list(overtime)
    who = " and ".join(names) if len(names) <= 2 else f"{', '.join(names[:2])} and others"
    detail = (
        f"{sum(overtime.values()):g} overtime hours (1.5x for employees)"
        f" · go-live {fmt.day(finish)}{_versus(inp, finish)}"
    )
    return Remedy("overtime", f"Overtime: {who} up to 25% over their hours", detail, extra, finish)


def _deadline_remedy(
    inp: PlanningInput, variant: PlanningInput, outcome: SolveOutcome, extra: int
) -> Remedy | None:
    finish = _finish(variant, outcome)
    deadline = inp.constraints.must_finish_by
    if deadline is not None and finish > deadline:
        slip = fmt.plural((finish - deadline).days, "day")
        title = f"Move go-live to {fmt.short_day(finish)} (+{slip})"
    elif deadline is not None:
        title = f"Let the phase milestones slip; go-live {fmt.short_day(finish)} still holds"
    else:
        title = "Let the phase milestones slip"
    parts = [
        f"Go-live {fmt.day(finish)}",
        "contractor included" if _contractor_hours(variant, outcome) else "no contractor",
        f"about {fmt.money(extra)}",
    ]
    if any(milestone.mode == "hard" for milestone in inp.milestones):
        parts.append("phase milestones may slip (soft)")
    return Remedy("move_deadline", title, " · ".join(parts), extra, finish)


def _accept_remedy(inp: PlanningInput, current: SolveOutcome) -> Remedy:
    """Keep this plan and its later date: nothing to re-solve, nothing extra to pay."""
    finish = _finish(inp, current)
    target = target_of(inp)
    assert target is not None  # noqa: S101 - only offered when the plan slips past it
    slip = workdays_late(inp.start_date, target, finish, inp.constraints.working_days_per_week)
    detail = (
        f"Keep this plan: go-live {fmt.day(finish)}, "
        f"{fmt.plural(slip, 'working day')} after {fmt.day(target)} · no extra cost"
    )
    return Remedy("move_deadline", f"Accept {fmt.short_day(finish)}", detail, 0, finish)


DESCRIBE = {
    "specialist": _specialist_remedy,
    "overtime": _overtime_remedy,
    "move_deadline": _deadline_remedy,
}


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
    share = SolveOptions(
        time_limit_s=max(options.time_limit_s / len(found), MIN_VARIANT_SECONDS),
        workers=options.workers,
        random_seed=options.random_seed,
    )
    base_finish = _finish(inp, current) if current is not None and solved else None
    base_cost = _cost(inp, current) if current is not None and solved else 0
    remedies = []
    for remedy_id, variant in found:
        if control is not None and control.stopped:
            break
        outcome = solve(variant, share, control, explain=False)
        if outcome.plan is None:
            continue
        if base_finish is not None and _finish(variant, outcome) >= base_finish:
            continue
        remedy = DESCRIBE[remedy_id](inp, variant, outcome, _cost(variant, outcome) - base_cost)
        if remedy is not None:
            remedies.append(remedy)
    if current is not None and solved:
        remedies.append(_accept_remedy(inp, current))
    return remedies
