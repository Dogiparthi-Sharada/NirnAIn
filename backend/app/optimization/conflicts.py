"""Plain-English texts for the hard constraint groups of an infeasible model.

Purpose: Each text states a fact read off the `Problem` (the dependency chain's earliest finish, the
cheapest qualified staffing, how long a part-timer needs) or, failing that, says the constraint
can't hold together with the others listed; nothing is invented. Each conflict also carries its
group's kind and the same fact as a short clause (`brief`), which the "Also tried" reasons reuse.
How it fits: `builder.py` attaches these lazily (via `functools.partial`) to the explanation model's
assumption literals; `blockers.explain_infeasibility` reports them; remedy_options.py words the
options that don't relax them; layer: optimization.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Sequence

from app.optimization import formatting as fmt
from app.optimization.model import Dependency, Milestone, SkillRequirement, Task
from app.optimization.outcome import Conflict, Resource
from app.optimization.problem import Problem

GENERIC = Conflict(
    "Hard constraints",
    "The hard constraints can't all hold at once. Try making the deadline or budget soft.",
)
LABOR_CAP = "Labor cap"  # the label builder.py gives the labor cap (the other is "Budget cap")


def names(people: Sequence[str]) -> str:
    """'A and B', or 'A, B and 3 others'."""
    if len(people) <= 2:
        return " and ".join(people)
    return f"{', '.join(people[:2])} and {fmt.plural(len(people) - 2, 'other')}"


def deadline(problem: Problem) -> Conflict:
    """The go-live date: the dependency chain alone, or not enough capacity by then."""
    due = problem.planning_input.constraints.must_finish_by
    name = f"Deadline: must finish by {fmt.day(due)}" if due else "Deadline"
    if problem.deadline is not None and problem.baseline.makespan > problem.deadline:
        critical = fmt.day(problem.finish_date(problem.baseline.makespan))
        return Conflict(
            name,
            f"The dependency chain alone runs until {critical}, even with unlimited people.",
            "deadline",
            f"the dependency chain alone runs until {critical}",
        )
    by = fmt.day(due) if due else "the deadline"
    return Conflict(
        name,
        "There isn't enough qualified capacity to finish all work by then "
        "together with the other constraints listed here.",
        "deadline",
        f"there isn't enough qualified capacity to finish by {by}",
    )


def milestone(problem: Problem, due: Milestone, tasks: Sequence[Task]) -> Conflict:
    """A hard phase milestone, with the earliest its work can finish if dependencies decide."""
    name = f"Milestone: {due.name} by {fmt.day(due.due_date)}"
    earliest = max((problem.baseline.tasks[task.id].end for task in tasks), default=0)
    if earliest > problem.milestone_ends[due.id]:
        finish = fmt.day(problem.finish_date(earliest))
        return Conflict(
            name,
            f"Its work can't finish before {finish} because of dependencies.",
            "milestone",
            f"the work for {due.name} can't finish before {finish}",
        )
    return Conflict(
        name,
        "Its work can't be staffed in time together with the other constraints listed here.",
        "milestone",
        f"the work for {due.name} can't be staffed by {fmt.day(due.due_date)}",
    )


def budget(problem: Problem, label: str, amount: int, held: int = 0) -> Conflict:
    """A hard budget (less the reserve `held` back) or labor cap, with the cheapest
    qualified staffing when that is over."""
    cheapest = sum(
        min(
            (problem.cost[task.id, resource.id] for resource in problem.allowed(task)),
            default=0,
        )
        for task in problem.tasks.values()
    )
    name = f"{label}: {fmt.money(amount)}"
    kind = "labor" if label == LABOR_CAP else "budget"
    cap = amount - held
    room = (
        f"the {fmt.money(cap)} left after holding back the {fmt.money(held)} reserve"
        if held
        else "the cap"
    )
    # The clause names the limit as the PM set it: the cap, what the reserve leaves, the budget.
    if kind == "labor":
        limit = f"the {fmt.money(cap)} labor cap"
    elif held:
        limit = f"the {fmt.money(cap)} the budget leaves after its reserve"
    else:
        limit = f"the {fmt.money(cap)} budget"
    if cheapest > cap:
        return Conflict(
            name,
            f"Even the cheapest qualified staffing costs about {fmt.money(cheapest)}, "
            f"{fmt.money(cheapest - cap)} more than {room}.",
            kind,
            f"the cheapest staffing costs {fmt.money(cheapest)}, over {limit}",
        )
    within = f" (the budget less its {fmt.money(held)} reserve)" if held else ""
    return Conflict(
        name,
        f"Staying within {fmt.money(cap)}{within} needs cheaper people than the other "
        "constraints listed here allow.",
        kind,
        f"staying within {limit} needs cheaper people",
    )


def skill(problem: Problem, task: Task, requirement: SkillRequirement) -> Conflict:
    """A required skill: who has it, or why nobody (on the team or outside) can cover it."""
    constraints = problem.planning_input.constraints
    name = f"Skill: {task.label} requires {requirement.skill_name} ≥ {requirement.min_proficiency}"
    able = [
        resource.name
        for resource in problem.resources
        if resource.kind == "employee"
        and resource.proficiency(requirement.skill_id) >= requirement.min_proficiency
    ]
    if able:
        return Conflict(
            name,
            f"Only {names(able)} qualify, and they can't take it on without breaking "
            "another constraint listed here.",
            "skill",
            f"only {names(able)} can do {task.label}, and they can't fit it in",
        )
    if not constraints.allow_contractors:
        tail = "and contractors are turned off."
    elif constraints.contractor_rate_cents is None:
        tail = "and no contractor rate is set."
    else:
        tail = "and no single contractor profile covers all of the task's skills."
    return Conflict(
        name,
        f"No one on the team has {requirement.skill_name} at level "
        f"{requirement.min_proficiency} or higher, {tail}",
        "skill",
        f"nobody on the team has {requirement.skill_name} at level "
        f"{requirement.min_proficiency} or higher",
    )


def _stretched_example(problem: Problem, resource: Resource) -> str:
    """' At those hours 1.2 Build takes 25 working days instead of 10.' or ''.

    Picks a task this person may take that their hours stretch, preferring
    one they currently own.
    """
    stretched = [
        task
        for task in problem.tasks.values()
        if resource in problem.allowed(task) and problem.modes[task.id, resource.id].stretched
    ]
    if not stretched:
        return ""
    owned = [
        task for task in stretched if problem.baseline.tasks[task.id].resource_id == resource.id
    ]
    task = (owned or stretched)[0]
    days = problem.modes[task.id, resource.id].duration_days
    return (
        f" At those hours {task.label} takes {fmt.plural(days, 'working day')} "
        f"instead of {task.duration_days}."
    )


def capacity(problem: Problem, resource: Resource) -> Conflict:
    """A person's hours: their capacity limit and how long their tasks take at those hours."""
    hours = f"{resource.weekly_capacity_hours:g}h/week"
    tail = (
        " on top of their commitments to other projects" if problem.blocks.get(resource.id) else ""
    )
    return Conflict(
        f"Capacity: {resource.name} ({hours})",
        f"{resource.name} can't take on the work they're needed for within {hours}{tail}."
        + _stretched_example(problem, resource),
        "capacity",
        f"{resource.name} can't fit their work into {hours}",
    )


def dependency(link: Dependency, predecessor: Task, successor: Task) -> Conflict:
    """A dependency between two work packages, read as a sentence."""
    what = {
        "FS": f"{successor.name} can't start until {predecessor.name} finishes",
        "SS": f"{successor.name} can't start until {predecessor.name} starts",
        "FF": f"{successor.name} can't finish until {predecessor.name} finishes",
        "SF": f"{successor.name} can't finish until {predecessor.name} starts",
    }[link.type]
    if link.lag_days > 0:
        what += f", plus {fmt.plural(link.lag_days, 'working day')}"
    elif link.lag_days < 0:
        what += f", minus {fmt.plural(-link.lag_days, 'working day')}"
    return Conflict(
        f"Dependency: {predecessor.label} → {successor.label}", what + ".", "dependency", what
    )
