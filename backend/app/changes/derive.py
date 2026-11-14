"""From a change to a re-plan: structured edits, what a change touches, and the overrides.

`from_edits` turns the Edit inputs / Progress update tabs into changes as they are.
`derive_overrides` maps the changes plus the answers to the `overrides` body of C1's
optimization run (budgetTotalCents, mustFinishBy, durationDays, hoursPerWeek); it returns
None until every question is answered, which is what keeps "Re-plan" locked.
`build_impact` assembles the `impact` JSON stored on the change request.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import date

from app.changes.context import ChangeContext
from app.changes.questions import KEEP, NONE, build_questions
from app.domain.changes import (
    AffectedPerson,
    AffectedTask,
    ChangeAffects,
    ChangeEdits,
    ChangeImpact,
    ChangeSource,
    DetectedChange,
    PlanOverrides,
)


class InvalidEditError(ValueError):
    """An edit refers to something that isn't part of the project; the message is user-facing."""


# --------------------------------------------------------------------------- edits / progress
def from_edits(edits: ChangeEdits, context: ChangeContext) -> list[DetectedChange]:
    """Structured edits become changes as they are: nothing is parsed or guessed."""
    out: list[DetectedChange] = []

    def add(**fields: object) -> None:
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
    return out


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

    for change in impact.changes:
        picked = _resolve(change, answers)
        if picked is None:
            continue
        target, to = picked
        if change.kind == "budget" and "budget" not in asked and isinstance(to, int | float):
            budget = int(to)
        elif change.kind == "deadline" and "deadline" not in asked and isinstance(to, str):
            deadline = date.fromisoformat(to)
        elif change.kind == "duration" and target is not None and isinstance(to, int | float):
            durations[str(target)] = int(to)
        elif change.kind == "capacity" and target is not None and isinstance(to, int | float):
            hours[str(target)] = float(to)
    return PlanOverrides(
        budget_total_cents=budget,
        must_finish_by=deadline,
        duration_days=durations or None,
        hours_per_week=hours or None,
    )


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
        elif change.kind == "capacity":
            targets = (
                [change.target] if change.target else [option.id for option in change.candidates]
            )
            for person_id in targets:
                people.add(person_id)
                task_ids |= {task.id for task in context.tasks if task.owner_id == person_id}
    tasks = [task for task in context.tasks if task.id in task_ids]
    people |= {task.owner_id for task in tasks if task.owner_id is not None}
    return ChangeAffects(
        tasks=[AffectedTask(id=task.id, code=task.code, name=task.name) for task in tasks],
        people=[
            AffectedPerson(id=person_id.id, name=person_id.name)
            for person_id in context.people
            if person_id.id in people
        ],
        cost_cents=context.plan_cost_cents,
        completion_date=context.plan_finish,
    )


def _reach(start: uuid.UUID, successors: dict[uuid.UUID, list[uuid.UUID]]) -> set[uuid.UUID]:
    seen = {start}
    stack = [start]
    while stack:
        for follower in successors.get(stack.pop(), []):
            if follower not in seen:
                seen.add(follower)
                stack.append(follower)
    return seen


def build_impact(
    changes: list[DetectedChange], source: ChangeSource, context: ChangeContext, model: str
) -> ChangeImpact:
    return ChangeImpact(
        changes=changes,
        questions=build_questions(changes, source, context),
        affects=build_affects(changes, context),
        model=model,
    )
