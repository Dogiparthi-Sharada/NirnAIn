"""Questions a change raises, what it touches, and the re-plan overrides it leads to."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from typing import Literal

from app.changes import text as tx
from app.changes.context import ChangeContext
from app.domain.changes import (
    AffectedPerson,
    AffectedTask,
    ChangeAffects,
    ChangeEdits,
    ChangeImpact,
    ChangeQuestion,
    ChangeSource,
    DetectedChange,
    PlanOverrides,
    QuestionOption,
)

KEEP = "keep"
NEW = "new"
NONE = "none"


class InvalidEditError(ValueError):
    """An edit refers to something that isn't part of the project; the message is user-facing."""


# --------------------------------------------------------------------------- edits / progress
def from_edits(edits: ChangeEdits, ctx: ChangeContext) -> list[DetectedChange]:
    """Structured edits become changes as they are: nothing is parsed or guessed."""
    out: list[DetectedChange] = []

    def add(**kw: object) -> None:
        out.append(DetectedChange.model_validate({"id": f"c{len(out) + 1}", **kw}))

    if edits.budget_total_cents is not None and edits.budget_total_cents != ctx.budget_cents:
        add(kind="budget", from_=ctx.budget_cents, to=edits.budget_total_cents)
    if edits.must_finish_by is not None and edits.must_finish_by != ctx.deadline:
        add(
            kind="deadline",
            from_=ctx.deadline.isoformat() if ctx.deadline else None,
            to=edits.must_finish_by.isoformat(),
        )
    for node_id, days in (edits.duration_days or {}).items():
        t = ctx.task(node_id)
        if t is None:
            raise InvalidEditError("A duration edit refers to an unknown work package.")
        if days != t.duration_days:
            add(
                kind="duration",
                target=t.id,
                target_label=f"{t.code} {t.name}",
                from_=t.duration_days,
                to=days,
            )
    for employee_id, hours in (edits.hours_per_week or {}).items():
        p = ctx.person(employee_id)
        if p is None:
            raise InvalidEditError("An hours edit refers to someone who isn't on the team.")
        if not math.isclose(hours, p.hours_per_week):
            add(kind="capacity", target=p.id, target_label=p.name, from_=p.hours_per_week, to=hours)
    for node_id, pct in (edits.progress or {}).items():
        t = ctx.task(node_id)
        if t is None:
            raise InvalidEditError("A progress update refers to an unknown work package.")
        if pct == 0 or not t.duration_days:
            continue
        remaining = math.ceil(t.duration_days * (100 - pct) / 100)
        add(
            kind="duration",
            target=t.id,
            target_label=f"{t.code} {t.name}",
            from_=t.duration_days,
            to=remaining,
            note="Done. Nothing left to plan." if pct == 100 else f"{pct}% complete",
        )
    return out


# --------------------------------------------------------------------------- questions
def build_questions(
    changes: list[DetectedChange], source: ChangeSource, ctx: ChangeContext
) -> list[ChangeQuestion]:
    """Values from notes that conflict with ones you confirmed are asked, never overwritten.

    Your own edits and progress need no questions. A change whose work package or person
    is unclear always asks which one it means.
    """
    qs: list[ChangeQuestion] = []
    for c in changes:
        if c.target is None and c.candidates:
            qs.append(_target_question(c))
    if source != "notes":
        return qs

    budgets = [c for c in changes if c.kind == "budget" and isinstance(c.to, int | float)]
    if budgets:
        current = None if ctx.budget_cents is None else str(ctx.budget_cents)
        qs.append(_group_question("budget", budgets, ctx, lambda v: tx.fmt_money(int(v)), current))
    deadlines = [c for c in changes if c.kind == "deadline" and isinstance(c.to, str)]
    if deadlines:
        qs.append(
            _group_question(
                "deadline",
                deadlines,
                ctx,
                lambda v: tx.fmt_date(date.fromisoformat(v)),
                ctx.deadline.isoformat() if ctx.deadline else None,
            )
        )
    for c in changes:
        if c.target is None:
            continue
        if c.kind == "duration" and c.to != 0:
            qs.append(
                ChangeQuestion(
                    id=f"duration:{c.id}",
                    kind="duration",
                    change_id=c.id,
                    tier="blocking",
                    text=f"Effort for {c.target_label}",
                    why=f"{ctx.plan_label} uses {tx.fmt_days(_num(c.from_))}. "
                    "The notes change it; confirm before it is used.",
                    options=[
                        QuestionOption(value=NEW, label=f"Confirm {tx.fmt_days(_num(c.to))}"),
                        QuestionOption(value=KEEP, label=f"Keep {tx.fmt_days(_num(c.from_))}"),
                    ],
                )
            )
        elif c.kind == "capacity":
            qs.append(_capacity_question(c, ctx))
    return qs


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) else None


def _target_question(c: DetectedChange) -> ChangeQuestion:
    def fmt(v: int | float | None) -> str:
        return tx.fmt_days(v) if c.kind == "duration" else tx.fmt_hours(v)

    return ChangeQuestion(
        id=f"target:{c.id}",
        kind="target",
        change_id=c.id,
        tier="blocking",
        text="Which work package does this refer to?"
        if c.kind == "duration"
        else "Who does this refer to?",
        why="The notes don't name it exactly, so we won't guess.",
        options=[
            QuestionOption(value=str(o.id), label=f"{o.label} ({fmt(o.from_)} → {fmt(o.to)})")
            for o in c.candidates
        ]
        + [QuestionOption(value=NONE, label="None of these")],
    )


def _group_question(
    kind: Literal["budget", "deadline"],
    changes: list[DetectedChange],
    ctx: ChangeContext,
    fmt: Callable[[str], str],
    current: str | None,
) -> ChangeQuestion:
    """One question for all budget (or deadline) values the notes give, plus "keep"."""
    values: list[str] = tx.unique(str(c.to) for c in changes)
    noun = "budget" if kind == "budget" else "deadline"
    if current is None:
        text = f"The notes give a {noun}. Which should the new plan use?"
        keep_label = f"Don't set a {noun}"
    else:
        text = (
            f"This conflicts with a value you confirmed. {ctx.plan_label} uses {fmt(current)}. "
            "Which should the new plan use?"
        )
        keep_label = f"Keep {fmt(current)}"
    return ChangeQuestion(
        id=kind,
        kind=kind,
        change_id=changes[0].id,
        tier="blocking",
        text=text,
        options=[QuestionOption(value=v, label=f"Use {fmt(v)}") for v in values]
        + [QuestionOption(value=KEEP, label=keep_label)],
    )


def _capacity_question(c: DetectedChange, ctx: ChangeContext) -> ChangeQuestion:
    name = c.target_label or "this person"
    if c.to == 0:
        return ChangeQuestion(
            id=f"capacity:{c.id}",
            kind="capacity",
            change_id=c.id,
            tier="high",
            text=f"Take {name} off the plan?",
            why=c.note
            or f"Their work moves to others in the re-plan. {ctx.plan_label} keeps "
            "them until you approve.",
            options=[
                QuestionOption(value=NEW, label=f"Yes, take {tx.first_name(name)} off"),
                QuestionOption(value=KEEP, label=f"No, keep {tx.first_name(name)}"),
            ],
        )
    return ChangeQuestion(
        id=f"capacity:{c.id}",
        kind="capacity",
        change_id=c.id,
        tier="high",
        text=f"Hours per week for {name}",
        why=f"{ctx.plan_label} uses {tx.fmt_hours(_num(c.from_))} a week.",
        options=[
            QuestionOption(value=NEW, label=f"Use {tx.fmt_hours(_num(c.to))}"),
            QuestionOption(value=KEEP, label=f"Keep {tx.fmt_hours(_num(c.from_))}"),
        ],
    )


def validate_answers(questions: list[ChangeQuestion], answers: dict[str, str]) -> str | None:
    """None when every answer matches a question and one of its options, else the problem."""
    by_id = {q.id: q for q in questions}
    for qid, value in answers.items():
        q = by_id.get(qid)
        if q is None:
            return f"Unknown question: {qid}"
        if value not in {o.value for o in q.options}:
            return f"Not an option for {qid}: {value}"
    return None


# --------------------------------------------------------------------------- overrides
def derive_overrides(impact: ChangeImpact, answers: dict[str, str]) -> PlanOverrides | None:
    """The re-plan overrides, once every question is answered (None before that)."""
    if any(q.id not in answers for q in impact.questions):
        return None
    asked = {q.id for q in impact.questions}
    budget: int | None = None
    deadline: date | None = None
    durations: dict[str, int] = {}
    hours: dict[str, float] = {}

    if answers.get("budget", KEEP) != KEEP:
        budget = int(answers["budget"])
    if answers.get("deadline", KEEP) != KEEP:
        deadline = date.fromisoformat(answers["deadline"])

    for c in impact.changes:
        picked = _resolve(c, answers)
        if picked is None:
            continue
        target, to = picked
        if c.kind == "budget" and "budget" not in asked and isinstance(to, int | float):
            budget = int(to)
        elif c.kind == "deadline" and "deadline" not in asked and isinstance(to, str):
            deadline = date.fromisoformat(to)
        elif c.kind == "duration" and target is not None and isinstance(to, int | float):
            durations[str(target)] = int(to)
        elif c.kind == "capacity" and target is not None and isinstance(to, int | float):
            hours[str(target)] = float(to)
    return PlanOverrides(
        budget_total_cents=budget,
        must_finish_by=deadline,
        duration_days=durations or None,
        hours_per_week=hours or None,
    )


def _resolve(
    c: DetectedChange, answers: dict[str, str]
) -> tuple[uuid.UUID | None, int | float | str | None] | None:
    """(target, new value) the answers settle on; None when the change is not used."""
    target, to = c.target, c.to
    if target is None and c.candidates:
        pick = answers.get(f"target:{c.id}", NONE)
        option = next((o for o in c.candidates if str(o.id) == pick), None)
        if option is None:
            return None
        target, to = option.id, option.to
    if answers.get(f"{c.kind}:{c.id}") == KEEP:
        return None
    return target, to


# --------------------------------------------------------------------------- affects
def build_affects(changes: list[DetectedChange], ctx: ChangeContext) -> ChangeAffects:
    """Work packages a change touches (plus everything after them) and their people."""
    succ: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for a, b in ctx.edges:
        succ[a].append(b)
    task_ids: set[uuid.UUID] = set()
    people: set[uuid.UUID] = set()
    for c in changes:
        if c.kind == "duration":
            roots = [c.target] if c.target else [o.id for o in c.candidates]
            for root in roots:
                task_ids |= _reach(root, succ)
        elif c.kind == "capacity":
            targets = [c.target] if c.target else [o.id for o in c.candidates]
            for p in targets:
                people.add(p)
                task_ids |= {t.id for t in ctx.tasks if t.owner_id == p}
    tasks = [t for t in ctx.tasks if t.id in task_ids]
    people |= {t.owner_id for t in tasks if t.owner_id is not None}
    return ChangeAffects(
        tasks=[AffectedTask(id=t.id, code=t.code, name=t.name) for t in tasks],
        people=[AffectedPerson(id=p.id, name=p.name) for p in ctx.people if p.id in people],
        cost_cents=ctx.plan_cost_cents,
        completion_date=ctx.plan_finish,
    )


def _reach(start: uuid.UUID, succ: dict[uuid.UUID, list[uuid.UUID]]) -> set[uuid.UUID]:
    seen = {start}
    stack = [start]
    while stack:
        for nxt in succ.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def build_impact(
    changes: list[DetectedChange], source: ChangeSource, ctx: ChangeContext, model: str
) -> ChangeImpact:
    return ChangeImpact(
        changes=changes,
        questions=build_questions(changes, source, ctx),
        affects=build_affects(changes, ctx),
        model=model,
    )
