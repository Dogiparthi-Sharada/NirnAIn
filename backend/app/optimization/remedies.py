"""Priced remedies for an infeasible plan ("What it would take").

When the hard constraints can't all hold, the worker re-solves relaxed
copies of the frozen input. Each copy changes exactly one thing the project
manager decides, so every option "changes as little as possible":

* `specialist`: allow outside specialists for skills nobody on the team has.
  Offered only when some work package has no qualified team member and
  specialists aren't allowed yet; priced at the contractor rate, or the
  $175/h estimate when no rate was given.
* `move_deadline`: let go-live (and hard phase milestones) move. The
  remedy names the date the relaxed plan actually reaches.

A variant that is still infeasible is not offered. Variants skip the
infeasibility explanation and share the run's time limit. A run created
with `remedy` applies the same relaxation up front (`apply_remedy`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal

from app.optimization import formatting as fmt
from app.optimization.calendar import workday_date
from app.optimization.costing import plan_cost
from app.optimization.load import allocation_blocks
from app.optimization.model import PlanningInput, Resource, SolveOutcome, Task
from app.optimization.solver import SolveControl, SolveOptions, solve

DEFAULT_SPECIALIST_RATE_CENTS = 17_500  # $175/h until the PM confirms a rate
MIN_VARIANT_SECONDS = 1.0

RemedyId = Literal["specialist", "move_deadline"]


@dataclass(frozen=True, slots=True)
class Remedy:
    """One priced way out; `cost_cents` is the whole relaxed plan's labor cost."""

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


def uncovered_tasks(inp: PlanningInput) -> list[Task]:
    """Work packages needing skills no single team member fully has."""
    return [
        task
        for task in inp.tasks
        if task.skills and not any(covers(employee.skills, task) for employee in inp.employees)
    ]


def allow_specialists(inp: PlanningInput) -> PlanningInput:
    """Outside specialists allowed, at the contractor rate or the $175/h estimate."""
    constraints = inp.constraints
    rate = constraints.contractor_rate_cents or DEFAULT_SPECIALIST_RATE_CENTS
    return replace(
        inp,
        constraints=replace(constraints, allow_contractors=True, contractor_rate_cents=rate),
    )


def move_deadline(inp: PlanningInput) -> PlanningInput:
    """Go-live and every phase milestone become soft: they may slip, at a penalty per day.

    The penalty keeps the solver finishing as early as it can, so the
    relaxed plan's completion date is the earliest date go-live can move to.
    """
    milestones = tuple(replace(milestone, mode="soft") for milestone in inp.milestones)
    constraints = replace(inp.constraints, deadline_mode="soft")
    return replace(inp, constraints=constraints, milestones=milestones)


def apply_remedy(inp: PlanningInput, remedy: RemedyId | None) -> PlanningInput:
    """The relaxation a PM picked from "What it would take", for one run."""
    if remedy == "specialist":
        return allow_specialists(inp)
    if remedy == "move_deadline":
        return move_deadline(inp)
    return inp


def variants(inp: PlanningInput) -> list[tuple[RemedyId, PlanningInput]]:
    """The relaxed inputs worth re-solving for this infeasible input."""
    constraints = inp.constraints
    found: list[tuple[RemedyId, PlanningInput]] = []
    specialists_on = constraints.allow_contractors and constraints.contractor_rate_cents is not None
    if not specialists_on and uncovered_tasks(inp):
        found.append(("specialist", allow_specialists(inp)))
    hard_deadline = constraints.must_finish_by is not None and constraints.deadline_mode == "hard"
    if hard_deadline or any(milestone.mode == "hard" for milestone in inp.milestones):
        found.append(("move_deadline", move_deadline(inp)))
    return found


# --------------------------------------------------------------------------- pricing
def _finish(inp: PlanningInput, outcome: SolveOutcome) -> date:
    assert outcome.plan is not None  # noqa: S101 - callers check
    per_week = inp.constraints.working_days_per_week
    return workday_date(inp.start_date, max(outcome.plan.makespan - 1, 0), per_week)


def _costs(inp: PlanningInput, outcome: SolveOutcome) -> tuple[int, int, list[Resource]]:
    """(total labor cost with the overtime premium, specialist cost, specialist profiles used)."""
    assert outcome.plan is not None  # noqa: S101 - callers check
    tasks = {task.id: task for task in inp.tasks}
    resources = {resource.id: resource for resource in outcome.resources}
    specialist_cost = 0
    used: dict[str, Resource] = {}
    for scheduled in outcome.plan.tasks.values():
        resource = resources.get(scheduled.resource_id) if scheduled.resource_id else None
        if resource is not None and resource.kind == "contractor":
            specialist_cost += round(tasks[scheduled.task_id].hours * resource.rate_cents)
            used[resource.id] = resource
    total = plan_cost(inp, resources, outcome.plan, allocation_blocks(inp))
    return total, specialist_cost, list(used.values())


def _versus_deadline(finish: date, deadline: date | None) -> str:
    if deadline is None:
        return ""
    if finish == deadline:
        return f" (on the {fmt.short_day(deadline)} deadline)"
    if finish < deadline:
        return f" ({fmt.plural((deadline - finish).days, 'day')} before {fmt.short_day(deadline)})"
    return f" ({fmt.plural((finish - deadline).days, 'day')} after {fmt.short_day(deadline)})"


def _specialist_remedy(inp: PlanningInput, variant: PlanningInput, outcome: SolveOutcome) -> Remedy:
    finish = _finish(variant, outcome)
    total, specialist_cost, used = _costs(variant, outcome)
    # Contractor profiles are named "Contractor: SAP specialist" by build_resources.
    skills = [
        profile.name.removeprefix("Contractor: ").removesuffix(" specialist") for profile in used
    ]
    estimate = fmt.money_short(specialist_cost)
    if len(skills) == 1:
        title = f"Contract one {skills[0]} specialist, est. {estimate}"
    elif skills:
        title = f"Contract {len(skills)} outside specialists ({', '.join(skills)}), est. {estimate}"
    else:
        title = "Allow an outside specialist"
    rate = variant.constraints.contractor_rate_cents or DEFAULT_SPECIALIST_RATE_CENTS
    rate_note = (
        f"specialist rate estimated at ${rate / 100:,.0f}/h"
        if inp.constraints.contractor_rate_cents is None
        else f"specialist at ${rate / 100:,.0f}/h"
    )
    detail = (
        f"Go-live {fmt.day(finish)}{_versus_deadline(finish, inp.constraints.must_finish_by)}"
        f" · about {fmt.money(total)} in total ({rate_note})"
    )
    return Remedy("specialist", title, detail, total, finish)


def _deadline_remedy(inp: PlanningInput, variant: PlanningInput, outcome: SolveOutcome) -> Remedy:
    finish = _finish(variant, outcome)
    total, specialist_cost, _ = _costs(variant, outcome)
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
        "outside specialist included" if specialist_cost else "no outside specialist",
        f"about {fmt.money(total)}",
    ]
    if any(milestone.mode == "hard" for milestone in inp.milestones):
        parts.append("phase milestones may slip (soft)")
    return Remedy("move_deadline", title, " · ".join(parts), total, finish)


def price_remedies(
    inp: PlanningInput, options: SolveOptions, control: SolveControl | None = None
) -> list[Remedy]:
    """Re-solve each relaxed variant; return the ones that produce a plan. CPU-bound."""
    found = variants(inp)
    if not found:
        return []
    share = SolveOptions(
        time_limit_s=max(options.time_limit_s / len(found), MIN_VARIANT_SECONDS),
        workers=options.workers,
        random_seed=options.random_seed,
    )
    remedies = []
    for remedy_id, variant in found:
        if control is not None and control.stopped:
            break
        outcome = solve(variant, share, control, explain=False)
        if outcome.plan is None:
            continue
        describe = _specialist_remedy if remedy_id == "specialist" else _deadline_remedy
        remedies.append(describe(inp, variant, outcome))
    return remedies
