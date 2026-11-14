"""Deterministic "Find what changed" (used when no AI key is configured, or as a fallback).

Reads new meeting notes against the project's inputs. It only reports what a sentence
states (money → budget, a date next to a deadline word → deadline, a work package with
"N weeks" or "done" → duration, a person with "N hours" or "leaves" → capacity, "has to
be added" → scope) and never invents values. Anything it can't pin down becomes a
question (see `app.changes.derive`).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, timedelta

from app.changes import text as tx
from app.changes.context import ChangeContext, PersonRef, TaskRef
from app.domain.changes import DetectedChange, TargetOption

_BUDGET_WORDS = re.compile(r"\b(budget|funding|funds|spend|cap|ceiling|financ)", re.I)
_DEADLINE_WORDS = re.compile(
    r"\b(go[- ]?live|deadline|launch|cut[- ]?over|release|due|finish(?:es|ed)? by|end date"
    r"|complete[ds]? by|deliver(?:ed|y)? by|wrap(?:s|ped)? up by|ship(?:s|ped)? by|target date)",
    re.I,
)
_LATER = re.compile(
    r"\b(slip(?:s|ped|ping)?|delay(?:s|ed)?|late|later|behind|push(?:es|ed)?(?: back| out)?"
    r"|extend(?:s|ed)?|move[ds]? (?:out|back)|longer|more|extra|additional|another|overrun"
    r"|add(?:s|ed)?)\b",
    re.I,
)
_EARLIER = re.compile(
    r"\b(early|earlier|ahead|faster|shorter|less|fewer|sooner|saves?|saved|pull(?:s|ed)? in)\b",
    re.I,
)
_DONE = re.compile(
    r"\b(is|are|was|were|now|been|got)?\s*(done|complete|completed|finished|delivered"
    r"|wrapped up)\b",
    re.I,
)
_LEAVES = re.compile(
    r"\b(leav(?:e|es|ing)|left|quit(?:s|ting)?|resign(?:s|ed|ing)?|unavailable|on leave"
    r"|parental leave|sick leave|out of office|no longer (?:available|on))\b",
    re.I,
)
_SCOPE = re.compile(
    r"\b((?:has|have|needs?|must) to be added|must be added|needs? adding|add(?:s|ed|ing)? .{0,60}"
    r"\b(?:as a|to the scope|requirement)|new requirement|also (?:need|want|require)s?"
    r"|must (?:include|support)|(?:drop|remove|descope|cut)(?:s|ped|d)? .{0,40}\bscope)",
    re.I,
)
_ON_TRACK = re.compile(r"\bon (track|schedule)\b", re.I)


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"c{self.n}"


def detect(text: str, ctx: ChangeContext) -> list[DetectedChange]:
    ids = _Ids()
    year = (ctx.deadline or ctx.plan_finish or ctx.today).year
    rare = _rare_tokens(ctx.tasks)
    out: list[DetectedChange] = []
    for s in tx.sentences(text):
        found: list[DetectedChange] = []
        found += _budget(s, ctx, ids)
        found += _deadline(s, ctx, ids, year)
        found += _capacity(s, ctx, ids)
        if not any(c.kind == "capacity" for c in found):
            found += _duration(s, ctx, ids, rare)
        if not any(c.kind in ("duration", "deadline") for c in found):
            found += _deadline_shift(s, ctx, ids)
        if not found and tx.has(s.body, _SCOPE):
            found.append(
                DetectedChange(
                    id=ids.next(),
                    kind="scope",
                    to=s.body,
                    quote=s.body,
                    speaker=s.speaker,
                    confidence=0.7,
                    note=(
                        "Re-planning doesn't add work. Add it as a work package in WBS Builder, "
                        "then record the change again."
                    ),
                )
            )
        out += found
    return out


# --------------------------------------------------------------------------- budget / deadline
def _budget(s: tx.Sentence, ctx: ChangeContext, ids: _Ids) -> list[DetectedChange]:
    amounts = tx.money_cents(s.body)
    if not amounts:
        return []
    keyword = tx.has(s.text, _BUDGET_WORDS)
    # Several amounts in one sentence: the last one is usually the new value ("from $X to $Y").
    cents = amounts[-1]
    if cents == ctx.budget_cents:
        return []
    return [
        DetectedChange(
            id=ids.next(),
            kind="budget",
            from_=ctx.budget_cents,
            to=cents,
            quote=s.body,
            speaker=s.speaker,
            confidence=0.9 if keyword else 0.5,
        )
    ]


def _deadline(s: tx.Sentence, ctx: ChangeContext, ids: _Ids, year: int) -> list[DetectedChange]:
    if not tx.has(s.text, _DEADLINE_WORDS):
        return []
    found = tx.dates(s.body, year)
    if not found:
        return []
    new = found[-1]
    if new == ctx.deadline:
        return []
    return [
        DetectedChange(
            id=ids.next(),
            kind="deadline",
            from_=ctx.deadline.isoformat() if ctx.deadline else None,
            to=new.isoformat(),
            quote=s.body,
            speaker=s.speaker,
            confidence=0.85,
        )
    ]


def _deadline_shift(s: tx.Sentence, ctx: ChangeContext, ids: _Ids) -> list[DetectedChange]:
    """ "Go-live slips two weeks": the deadline moves by a stated span."""
    if ctx.deadline is None or not tx.has(s.text, _DEADLINE_WORDS):
        return []
    spans = tx.spans_days(s.body)
    if not spans:
        return []
    sign = -1 if tx.has(s.body, _EARLIER) else 1 if tx.has(s.body, _LATER) else 0
    if sign == 0:
        return []
    new = _add_workdays(ctx.deadline, sign * spans[0])
    return [
        DetectedChange(
            id=ids.next(),
            kind="deadline",
            from_=ctx.deadline.isoformat(),
            to=new.isoformat(),
            quote=s.body,
            speaker=s.speaker,
            confidence=0.7,
        )
    ]


def _add_workdays(d: date, days: int) -> date:
    step = 1 if days >= 0 else -1
    left = abs(days)
    while left:
        d += timedelta(days=step)
        if d.weekday() < 5:
            left -= 1
    return d


# --------------------------------------------------------------------------- work packages
def _rare_tokens(tasks: tuple[TaskRef, ...]) -> set[str]:
    """Name tokens used by at most two work packages: enough to suggest a match."""
    counts: Counter[str] = Counter()
    for t in tasks:
        counts.update(tx.distinctive(t.name))
    return {tok for tok, n in counts.items() if n <= 2 and len(tok) > 2}


def match_tasks(
    sentence: str, tasks: tuple[TaskRef, ...], rare: set[str]
) -> tuple[list[TaskRef], list[TaskRef]]:
    """(exact, partial) work packages a sentence mentions."""
    words = tx.tokens(sentence)
    lower = sentence.lower()
    exact: list[TaskRef] = []
    partial: list[TaskRef] = []
    for t in tasks:
        name = tx.distinctive(t.name)
        if (
            tx.code_in(t.code, sentence)
            or t.name.lower() in lower
            or (len(name) >= 2 and name <= words)
        ):
            exact.append(t)
        elif name & words & rare:
            partial.append(t)
    # A package whose whole name matched beats ones that only share a word.
    return exact, ([] if exact else partial)


def _duration(
    s: tx.Sentence, ctx: ChangeContext, ids: _Ids, rare: set[str]
) -> list[DetectedChange]:
    if tx.has(s.body, _ON_TRACK):
        return []
    exact, partial = match_tasks(s.text, ctx.tasks, rare)
    hits = exact or partial
    if not hits:
        return []
    spans = tx.spans_days(s.body)
    done = not spans and tx.has(s.body, _DONE)
    if not spans and not done:
        return []

    def new_value(t: TaskRef) -> int | None:
        if done:
            return 0
        n = spans[0]
        if tx.has(s.body, _EARLIER):
            return None if t.duration_days is None else max(t.duration_days - n, 1)
        if tx.has(s.body, _LATER):
            return None if t.duration_days is None else t.duration_days + n
        return n

    options = [
        TargetOption(id=t.id, label=_task_label(t), from_=t.duration_days, to=v)
        for t in hits
        if (v := new_value(t)) is not None and v != t.duration_days
    ]
    if not options:
        return []
    confidence = 0.9 if len(exact) == 1 else 0.5
    if len(exact) == 1:
        o = options[0]
        return [
            DetectedChange(
                id=ids.next(),
                kind="duration",
                target=o.id,
                target_label=o.label,
                from_=o.from_,
                to=o.to,
                quote=s.body,
                speaker=s.speaker,
                confidence=confidence,
                note="Done. Nothing left to plan." if done else None,
            )
        ]
    return [
        DetectedChange(
            id=ids.next(),
            kind="duration",
            to=0 if done else None,
            quote=s.body,
            speaker=s.speaker,
            confidence=confidence,
            candidates=options,
            note="Done. Nothing left to plan." if done else None,
        )
    ]


def _task_label(t: TaskRef) -> str:
    return f"{t.code} {t.name}"


# --------------------------------------------------------------------------- people
def match_people(sentence: str, people: tuple[PersonRef, ...]) -> list[PersonRef]:
    words = set(re.findall(r"[a-z0-9']+", sentence.lower()))
    lower = sentence.lower()
    full = [p for p in people if p.name.lower() in lower]
    if full:
        return full
    firsts = Counter(tx.first_name(p.name).lower() for p in people)
    lasts = Counter(p.name.split()[-1].lower() for p in people if len(p.name.split()) > 1)
    out = []
    for p in people:
        first = tx.first_name(p.name).lower()
        last = p.name.split()[-1].lower() if len(p.name.split()) > 1 else None
        if (first in words and firsts[first] == 1) or (
            last is not None and last in words and lasts[last] == 1
        ):
            out.append(p)
    return out


def _capacity(s: tx.Sentence, ctx: ChangeContext, ids: _Ids) -> list[DetectedChange]:
    people = match_people(s.body, ctx.people)
    if len(people) != 1:
        return []
    p = people[0]
    leaves = tx.has(s.body, _LEAVES)
    hrs = tx.hours(s.body)
    if leaves:
        new: float = 0
    elif hrs:
        new = hrs[0]
    else:
        return []
    if math.isclose(new, p.hours_per_week):
        return []
    period = leaves and bool(tx.spans_days(s.body) or tx.dates(s.body, ctx.today.year))
    return [
        DetectedChange(
            id=ids.next(),
            kind="capacity",
            target=p.id,
            target_label=p.name,
            from_=p.hours_per_week,
            to=new,
            quote=s.body,
            speaker=s.speaker,
            confidence=0.6 if period else 0.85,
            note=(
                "The notes mention a limited period. A re-plan can only take them off the "
                "whole project."
                if period
                else None
            ),
        )
    ]
