"""Each priced option's numbers and wording ("Your call", round-2 D2 and round 3 R6).

Purpose: Say what each relaxed plan takes (contractor hours and rate, overtime, the new go-live, the
extra cost) and offer an option only when its plan uses what the option adds.
How it fits: `remedies.price_remedies` re-solves relaxed copies and calls these to build the
`Remedy` entries; `combined_remedy` lists the changes "Change these together" needs;
layer: optimization.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from app.optimization import formatting as fmt
from app.optimization.calendar import workday_date, workdays_late
from app.optimization.costing import overtime_hours, plan_cost, spend_limit_cents
from app.optimization.load import allocation_blocks
from app.optimization.model import PlanningInput, target_of
from app.optimization.outcome import Resource, SolveOutcome
from app.optimization.resources import CONTRACTOR_ID

DEFAULT_SPECIALIST_RATE_CENTS = 17_500  # $175/h until the PM confirms a rate

RemedyId = Literal["specialist", "overtime", "move_deadline", "combined"]
ChangeKind = Literal["deadline", "budget", "specialist"]


@dataclass(frozen=True, slots=True)
class Change:
    """One thing the combined option changes, e.g. ('deadline', 'Move go-live to Jun 11')."""

    kind: ChangeKind
    label: str


@dataclass(frozen=True, slots=True)
class Remedy:
    """One priced option; `cost_cents` is extra against this plan (whole plan if none)."""

    id: RemedyId
    title: str
    detail: str
    cost_cents: int | None
    completion_date: date | None
    changes: tuple[Change, ...] = ()  # what the combined option changes (R6)

    def to_json(self) -> dict[str, Any]:
        """The camelCase shape stored in `result.remedies`."""
        shape: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "detail": self.detail,
            "costCents": self.cost_cents,
            "completionDate": self.completion_date.isoformat() if self.completion_date else None,
        }
        if self.changes:
            shape["changes"] = [
                {"kind": change.kind, "label": change.label} for change in self.changes
            ]
        return shape


# --------------------------------------------------------------------------- pricing
def finish_of(planning_input: PlanningInput, outcome: SolveOutcome) -> date:
    """The plan's completion date (its last working day)."""
    assert outcome.plan is not None  # noqa: S101 - callers check
    per_week = planning_input.constraints.working_days_per_week
    return workday_date(planning_input.start_date, max(outcome.plan.makespan - 1, 0), per_week)


def _contractor_hours(
    planning_input: PlanningInput, outcome: SolveOutcome
) -> list[tuple[Resource, float]]:
    """Each contractor profile the plan uses, with the hours of work it takes."""
    assert outcome.plan is not None  # noqa: S101 - callers check
    tasks = {task.id: task for task in planning_input.tasks}
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


def cost_of(planning_input: PlanningInput, outcome: SolveOutcome) -> int:
    """The plan's labor cost, overtime premium included."""
    assert outcome.plan is not None  # noqa: S101 - callers check
    resources = {resource.id: resource for resource in outcome.resources}
    return plan_cost(planning_input, resources, outcome.plan, allocation_blocks(planning_input))


def _overtime(planning_input: PlanningInput, outcome: SolveOutcome) -> dict[str, float]:
    """Overtime hours by person's name, for those who work any."""
    assert outcome.plan is not None  # noqa: S101 - callers check
    blocks = allocation_blocks(planning_input)
    hours = {
        resource.name: overtime_hours(planning_input, outcome.plan, resource, blocks)
        for resource in outcome.resources
    }
    return {name: amount for name, amount in hours.items() if amount > 0}


def _versus(planning_input: PlanningInput, finish: date) -> str:
    """' (3 working days after Oct 30)' against the target date, or ''."""
    target = target_of(planning_input)
    if target is None:
        return ""
    per_week = planning_input.constraints.working_days_per_week
    after = workdays_late(planning_input.start_date, target, finish, per_week)
    before = workdays_late(planning_input.start_date, finish, target, per_week)
    if after:
        return f" ({fmt.plural(after, 'working day')} after {fmt.short_day(target)})"
    if before:
        return f" ({fmt.plural(before, 'working day')} before {fmt.short_day(target)})"
    return f" (on the {fmt.short_day(target)} target)"


def _specialist_remedy(
    planning_input: PlanningInput, variant: PlanningInput, outcome: SolveOutcome, extra: int
) -> Remedy | None:
    """Return the "Bring in a contractor" option, or None when the plan used no contractor hours."""
    used = _contractor_hours(variant, outcome)
    if not used:
        return None  # it didn't need a contractor: not this option's date
    finish = finish_of(variant, outcome)
    detail = (
        f"A contractor takes {_contractor_terms(planning_input, variant, used)}"
        f" · go-live {fmt.day(finish)}{_versus(planning_input, finish)}"
    )
    return Remedy("specialist", "Bring in a contractor", detail, extra, finish)


def _contractor_terms(
    planning_input: PlanningInput, variant: PlanningInput, used: list[tuple[Resource, float]]
) -> str:
    """'80 hours of the work at $175/h (estimate), covering SAP, which nobody on the team
    has'."""
    rate = variant.constraints.contractor_rate_cents or DEFAULT_SPECIALIST_RATE_CENTS
    estimate = " (estimate)" if planning_input.constraints.contractor_rate_cents is None else ""
    # Specialist profiles are named "Contractor: SAP specialist" (`resources.py`).
    gaps = [
        profile.name.removeprefix("Contractor: ").removesuffix(" specialist")
        for profile, _ in used
        if profile.id != CONTRACTOR_ID
    ]
    covering = f", covering {' and '.join(gaps)}, which nobody on the team has" if gaps else ""
    hours = sum(amount for _, amount in used)
    return f"{hours:g} hours of the work at ${rate / 100:,.0f}/h{estimate}{covering}"


def _overtime_remedy(
    planning_input: PlanningInput, variant: PlanningInput, outcome: SolveOutcome, extra: int
) -> Remedy | None:
    """Return the overtime option naming who works over their hours, or None without overtime."""
    overtime = _overtime(variant, outcome)
    if not overtime:
        return None
    finish = finish_of(variant, outcome)
    names = list(overtime)
    who = " and ".join(names) if len(names) <= 2 else f"{', '.join(names[:2])} and others"
    detail = (
        f"{sum(overtime.values()):g} overtime hours (1.5x for employees)"
        f" · go-live {fmt.day(finish)}{_versus(planning_input, finish)}"
    )
    return Remedy("overtime", f"Overtime: {who} up to 25% over their hours", detail, extra, finish)


def _deadline_remedy(
    planning_input: PlanningInput, variant: PlanningInput, outcome: SolveOutcome, extra: int
) -> Remedy | None:
    """Return the "Move go-live" option (or letting milestones slip) with its date and cost."""
    finish = finish_of(variant, outcome)
    deadline = planning_input.constraints.must_finish_by
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
    if any(milestone.mode == "hard" for milestone in planning_input.milestones):
        parts.append("phase milestones may slip (soft)")
    return Remedy("move_deadline", title, " · ".join(parts), extra, finish)


def accept_remedy(planning_input: PlanningInput, current: SolveOutcome) -> Remedy:
    """Keep this plan and its later date: nothing to re-solve, nothing extra to pay."""
    finish = finish_of(planning_input, current)
    target = target_of(planning_input)
    assert target is not None  # noqa: S101 - only offered when the plan slips past it
    slip = workdays_late(
        planning_input.start_date, target, finish, planning_input.constraints.working_days_per_week
    )
    detail = (
        f"Keep this plan: go-live {fmt.day(finish)}, "
        f"{fmt.plural(slip, 'working day')} after {fmt.day(target)} · no extra cost"
    )
    return Remedy("move_deadline", f"Accept {fmt.short_day(finish)}", detail, 0, finish)


# --------------------------------------------------------------------------- combined (R6)
def _date_changes(planning_input: PlanningInput, outcome: SolveOutcome) -> list[Change]:
    """The hard go-live date and hard phase milestones the relaxed plan misses."""
    assert outcome.plan is not None  # noqa: S101 - callers check
    constraints, plan = planning_input.constraints, outcome.plan
    per_week = constraints.working_days_per_week
    found = []
    finish = finish_of(planning_input, outcome)
    deadline = constraints.must_finish_by
    if constraints.deadline_mode == "hard" and deadline is not None and finish > deadline:
        slip = fmt.plural((finish - deadline).days, "day")
        found.append(
            Change(
                "deadline", f"Move go-live from {fmt.day(deadline)} to {fmt.day(finish)} (+{slip})"
            )
        )
    for milestone in planning_input.milestones:
        ends = [plan.tasks[task_id].end for task_id in milestone.task_ids if task_id in plan.tasks]
        if milestone.mode != "hard" or not ends:
            continue
        lands = workday_date(planning_input.start_date, max(ends) - 1, per_week)
        if lands > milestone.due_date:
            label = (
                f"Let {milestone.name} slip from {fmt.day(milestone.due_date)} to {fmt.day(lands)}"
            )
            found.append(Change("deadline", label))
    return found


def _budget_changes(planning_input: PlanningInput, outcome: SolveOutcome) -> list[Change]:
    """The hard budget (less its reserve) or labor cap the relaxed plan goes over."""
    constraints = planning_input.constraints
    if constraints.budget_mode != "hard":
        return []
    cost = cost_of(planning_input, outcome)
    found = []
    limit = spend_limit_cents(constraints)
    if limit is not None and cost > limit:
        allowed = (
            "the budget less its reserve"
            if limit < (constraints.budget_total_cents or 0)
            else "the budget"
        )
        found.append(
            Change(
                "budget",
                f"Spend {fmt.money(cost - limit)} more: this plan costs {fmt.money(cost)}, "
                f"{allowed} is {fmt.money(limit)}",
            )
        )
    cap = constraints.labor_cap_cents
    if cap is not None and cost > cap:
        found.append(
            Change(
                "budget", f"Raise the {fmt.money(cap)} labor cap: this plan costs {fmt.money(cost)}"
            )
        )
    return found


def _specialist_changes(
    planning_input: PlanningInput, variant: PlanningInput, outcome: SolveOutcome
) -> list[Change]:
    """Specialists the project couldn't use (not allowed, or no rate to price them)."""
    constraints = planning_input.constraints
    if constraints.allow_contractors and constraints.contractor_rate_cents is not None:
        return []
    used = _contractor_hours(variant, outcome)
    if not used:
        return []
    return [
        Change(
            "specialist",
            f"Bring in a contractor: {_contractor_terms(planning_input, variant, used)}",
        )
    ]


def combined_remedy(
    planning_input: PlanningInput, variant: PlanningInput, outcome: SolveOutcome, extra: int
) -> Remedy | None:
    """The option to change these together: what the relaxed plan needs, all at once."""
    changes = [
        *_date_changes(planning_input, outcome),
        *_budget_changes(planning_input, outcome),
        *_specialist_changes(planning_input, variant, outcome),
    ]
    if not changes:
        return None
    finish = finish_of(variant, outcome)
    detail = (
        f"No single change is enough; together these get a plan: go-live {fmt.day(finish)}"
        f"{_versus(planning_input, finish)} · about {fmt.money(extra)}"
    )
    return Remedy("combined", "Change these together", detail, extra, finish, tuple(changes))


DESCRIBE = {
    "specialist": _specialist_remedy,
    "overtime": _overtime_remedy,
    "move_deadline": _deadline_remedy,
    "combined": combined_remedy,
}
