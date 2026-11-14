"""From a change to a re-plan: structured edits, what a change touches, and the overrides.

Purpose: Edits and progress updates become changes as they are; `derive_overrides` maps the changes
plus the answers to the `overrides` body of the re-plan run and returns None until every question is
answered, which is what keeps "Re-plan" locked.
How it fits: Used by app/api/v1/changes.py and `to_change` in app/repositories/changes.py;
`build_impact` assembles the `impact` JSON stored on the change request; layer: changes.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import date

from app.changes.context import ChangeContext
from app.changes.questions import KEEP, NONE, build_questions
from app.changes.team import TeamEditError, is_addition, is_removal, member_overrides, team_edits
from app.domain.changes import (
    AffectedPerson,
    AffectedTask,
    ChangeAffects,
    ChangeEdits,
    ChangeImpact,
    ChangeKind,
    ChangeMode,
    ChangeSource,
    DetectedChange,
    PlanOverrides,
)

# Longest scope item text (scope_items.text check constraint).
MAX_REQUIREMENT_CHARS = 2000


class InvalidEditError(ValueError):
    """An edit refers to something that isn't part of the project; the message is user-facing."""


# --------------------------------------------------------------------------- edits / progress
def from_edits(edits: ChangeEdits, context: ChangeContext) -> list[DetectedChange]:
    """Structured edits become changes as they are: nothing is parsed or guessed."""
    out: list[DetectedChange] = []

    def add(**fields: object) -> None:
        """Append a change with the next sequential id."""
        out.append(DetectedChange.model_validate({"id": f"c{len(out) + 1}", **fields}))

    if edits.budget_total_cents is not None and edits.budget_total_cents != context.budget_cents:
        add(kind="budget", from_=context.budget_cents, to=edits.budget_total_cents)
    if edits.must_finish_by is not None and edits.must_finish_by != context.deadline:
        add(
            kind="deadline",
            from_=context.deadline.isoformat() if context.deadline else None,
            to=edits.must_finish_by.isoformat(),
        )
    for node_id, days in (edits.duration_days or {}).items():
        task = context.task(node_id)
        if task is None:
            raise InvalidEditError("A duration edit refers to an unknown work package.")
        if days != task.duration_days:
            add(
                kind="duration",
                target=task.id,
                target_label=f"{task.code} {task.name}",
                from_=task.duration_days,
                to=days,
            )
    for employee_id, hours in (edits.hours_per_week or {}).items():
        person = context.person(employee_id)
        if person is None:
            raise InvalidEditError("An hours edit refers to someone who isn't on the team.")
        if not math.isclose(hours, person.hours_per_week):
            add(
                kind="capacity",
                target=person.id,
                target_label=person.name,
                from_=person.hours_per_week,
                to=hours,
            )
    for node_id, percent in (edits.progress or {}).items():
        task = context.task(node_id)
        if task is None:
            raise InvalidEditError("A progress update refers to an unknown work package.")
        if percent == 0 or not task.duration_days:
            continue
        remaining = math.ceil(task.duration_days * (100 - percent) / 100)
        add(
            kind="duration",
            target=task.id,
            target_label=f"{task.code} {task.name}",
            from_=task.duration_days,
            to=remaining,
            note="Done. Nothing left to plan." if percent == 100 else f"{percent}% complete",
        )
    for fields in _team_edits(edits, context):
        add(**fields)
    return out


def _team_edits(edits: ChangeEdits, context: ChangeContext) -> list[dict[str, object]]:
    """Who joins and who leaves, with the team rules' violations as InvalidEditError."""
    try:
        return team_edits(edits, context)
    except TeamEditError as exc:
        raise InvalidEditError(str(exc)) from exc


# --------------------------------------------------------------------------- overrides
def derive_overrides(impact: ChangeImpact, answers: dict[str, str]) -> PlanOverrides | None:
    """The re-plan overrides, once every question is answered (None before that)."""
    if any(question.id not in answers for question in impact.questions):
        return None
    asked = {question.id for question in impact.questions}
    budget: int | None = None
    deadline: date | None = None
    durations: dict[str, int] = {}
    hours: dict[str, float] = {}

    if answers.get("budget", KEEP) != KEEP:
        budget = int(answers["budget"])
    if answers.get("deadline", KEEP) != KEEP:
        deadline = date.fromisoformat(answers["deadline"])
    # The policy stated with the chosen value ("$200,000 hard budget") goes with it.
    budget_mode = _stated_mode(impact.changes, "budget", answers.get("budget"))
    deadline_mode = _stated_mode(impact.changes, "deadline", answers.get("deadline"))

    for change in impact.changes:
        picked = _resolve(change, answers)
        if picked is None:
            continue
        target, to = picked
        if change.kind == "budget" and "budget" not in asked and isinstance(to, int | float):
            budget, budget_mode = int(to), change.mode
        elif change.kind == "deadline" and "deadline" not in asked and isinstance(to, str):
            deadline, deadline_mode = date.fromisoformat(to), change.mode
        elif change.kind == "duration" and target is not None and isinstance(to, int | float):
            durations[str(target)] = int(to)
        elif change.kind == "capacity" and target is not None and isinstance(to, int | float):
            hours[str(target)] = float(to)
    added, removed = member_overrides(impact.changes, answers)
    return PlanOverrides(
        budget_total_cents=budget,
        must_finish_by=deadline,
        duration_days=durations or None,
        hours_per_week=hours or None,
        add_members=added or None,
        remove_members=removed or None,
        budget_mode=budget_mode if budget is not None else None,
        deadline_mode=deadline_mode if deadline is not None else None,
    )


def _stated_mode(
    changes: list[DetectedChange], kind: ChangeKind, answer: str | None
) -> ChangeMode | None:
    """The policy the notes state with the value the answer picked (None if they don't)."""
    if answer is None or answer == KEEP:
        return None
    stated = [
        change.mode
        for change in changes
        if change.kind == kind and str(change.to) == answer and change.mode is not None
    ]
    return stated[-1] if stated else None


def _resolve(
    change: DetectedChange, answers: dict[str, str]
) -> tuple[uuid.UUID | None, int | float | str | None] | None:
    """(target, new value) the answers settle on; None when the change is not used."""
    target, to = change.target, change.to
    if target is None and change.candidates:
        pick = answers.get(f"target:{change.id}", NONE)
        option = next((option for option in change.candidates if str(option.id) == pick), None)
        if option is None:
            return None
        target, to = option.id, option.to
    if answers.get(f"{change.kind}:{change.id}") == KEEP:
        return None
    return target, to


# --------------------------------------------------------------------------- affects
def build_affects(changes: list[DetectedChange], context: ChangeContext) -> ChangeAffects:
    """Work packages a change touches (plus everything after them) and their people."""
    successors: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for predecessor, successor in context.edges:
        successors[predecessor].append(successor)
    task_ids: set[uuid.UUID] = set()
    people: set[uuid.UUID] = set()
    for change in changes:
        if change.kind == "duration":
            roots = (
                [change.target] if change.target else [option.id for option in change.candidates]
            )
            for root in roots:
                task_ids |= _reach(root, successors)
        elif change.kind == "capacity" or is_removal(change):
            targets = (
                [change.target] if change.target else [option.id for option in change.candidates]
            )
            for person_id in targets:
                people.add(person_id)
                task_ids |= {task.id for task in context.tasks if task.owner_id == person_id}
    tasks = [task for task in context.tasks if task.id in task_ids]
    people |= {task.owner_id for task in tasks if task.owner_id is not None}
    # Newcomers aren't on the team yet: listed under the employee reserved for them.
    newcomers = [
        AffectedPerson(id=change.target, name=change.member.full_name)
        for change in changes
        if is_addition(change) and change.target is not None and change.member is not None
    ]
    return ChangeAffects(
        tasks=[AffectedTask(id=task.id, code=task.code, name=task.name) for task in tasks],
        people=[
            AffectedPerson(id=person.id, name=person.name)
            for person in context.people
            if person.id in people
        ]
        + newcomers,
        cost_cents=context.plan_cost_cents,
        completion_date=context.plan_finish,
    )


def _reach(start: uuid.UUID, successors: dict[uuid.UUID, list[uuid.UUID]]) -> set[uuid.UUID]:
    """Return the start node and every node that follows it through the links."""
    seen = {start}
    stack = [start]
    while stack:
        for follower in successors.get(stack.pop(), []):
            if follower not in seen:
                seen.add(follower)
                stack.append(follower)
    return seen


def requirements_to_add(impact: ChangeImpact | None) -> list[str]:
    """Requirements the change notes as new: on approval they join the project's scope.

    Re-planning can't turn requirement text into work, so these are informational in the
    impact; any effort they need is a duration change on the work package they belong to.
    """
    if impact is None:
        return []
    return [
        change.to[:MAX_REQUIREMENT_CHARS]
        for change in impact.changes
        if change.kind == "scope" and change.action != "remove" and isinstance(change.to, str)
    ]


def build_impact(
    changes: list[DetectedChange], source: ChangeSource, context: ChangeContext, model: str
) -> ChangeImpact:
    """The stored impact: the changes, the questions they raise and what they touch."""
    return ChangeImpact(
        changes=changes,
        questions=build_questions(changes, source, context),
        affects=build_affects(changes, context),
        model=model,
    )
