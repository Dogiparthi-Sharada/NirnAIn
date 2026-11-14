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

from app.changes import parsing
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
    """Sequential change ids (c1, c2…) that questions and answers refer to."""

    def __init__(self) -> None:
        self.count = 0

    def next(self) -> str:
        self.count += 1
        return f"c{self.count}"


def detect(text: str, context: ChangeContext) -> list[DetectedChange]:
    """Changes the notes state, in reading order, each with its verbatim quote.

    Pure and deterministic: used when no AI key is configured and when the Claude call
    fails. Values come only from the sentence and the project (never invented).
    """
    ids = _Ids()
    year = (context.deadline or context.plan_finish or context.today).year
    rare = _rare_tokens(context.tasks)
    out: list[DetectedChange] = []
    for sentence in parsing.sentences(text):
        found: list[DetectedChange] = []
        found += _budget(sentence, context, ids)
        found += _deadline(sentence, context, ids, year)
        found += _capacity(sentence, context, ids)
        if not any(change.kind == "capacity" for change in found):
            found += _duration(sentence, context, ids, rare)
        if not any(change.kind in ("duration", "deadline") for change in found):
            found += _deadline_shift(sentence, context, ids)
        if not found and parsing.has(sentence.body, _SCOPE):
            found.append(
                DetectedChange(
                    id=ids.next(),
                    kind="scope",
                    to=sentence.body,
                    quote=sentence.body,
                    speaker=sentence.speaker,
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
def _budget(sentence: parsing.Sentence, context: ChangeContext, ids: _Ids) -> list[DetectedChange]:
    """A money amount → budget change. Without a budget word it is only a candidate
    (confidence 0.5); the notes' value is always confirmed through a question."""
    amounts = parsing.money_cents(sentence.body)
    if not amounts:
        return []
    keyword = parsing.has(sentence.text, _BUDGET_WORDS)
    # Several amounts in one sentence: the last one is usually the new value ("from $X to $Y").
    cents = amounts[-1]
    if cents == context.budget_cents:
        return []
    return [
        DetectedChange(
            id=ids.next(),
            kind="budget",
            from_=context.budget_cents,
            to=cents,
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=0.9 if keyword else 0.5,
        )
    ]


def _deadline(
    sentence: parsing.Sentence, context: ChangeContext, ids: _Ids, year: int
) -> list[DetectedChange]:
    """A date counts as a new deadline only next to a deadline word (a meeting date doesn't)."""
    if not parsing.has(sentence.text, _DEADLINE_WORDS):
        return []
    found = parsing.dates(sentence.body, year)
    if not found:
        return []
    new = found[-1]
    if new == context.deadline:
        return []
    return [
        DetectedChange(
            id=ids.next(),
            kind="deadline",
            from_=context.deadline.isoformat() if context.deadline else None,
            to=new.isoformat(),
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=0.85,
        )
    ]


def _deadline_shift(
    sentence: parsing.Sentence, context: ChangeContext, ids: _Ids
) -> list[DetectedChange]:
    """ "Go-live slips two weeks": the deadline moves by a stated span."""
    if context.deadline is None or not parsing.has(sentence.text, _DEADLINE_WORDS):
        return []
    spans = parsing.spans_days(sentence.body)
    if not spans:
        return []
    sign = (
        -1
        if parsing.has(sentence.body, _EARLIER)
        else 1
        if parsing.has(sentence.body, _LATER)
        else 0
    )
    if sign == 0:
        return []
    new = _add_workdays(context.deadline, sign * spans[0])
    return [
        DetectedChange(
            id=ids.next(),
            kind="deadline",
            from_=context.deadline.isoformat(),
            to=new.isoformat(),
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=0.7,
        )
    ]


def _add_workdays(current: date, days: int) -> date:
    step = 1 if days >= 0 else -1
    left = abs(days)
    while left:
        current += timedelta(days=step)
        if current.weekday() < 5:
            left -= 1
    return current


# --------------------------------------------------------------------------- work packages
def _rare_tokens(tasks: tuple[TaskRef, ...]) -> set[str]:
    """Name tokens used by at most two work packages: enough to suggest a match."""
    counts: Counter[str] = Counter()
    for task in tasks:
        counts.update(parsing.distinctive(task.name))
    return {token for token, uses in counts.items() if uses <= 2 and len(token) > 2}


def match_tasks(
    sentence: str, tasks: tuple[TaskRef, ...], rare: set[str]
) -> tuple[list[TaskRef], list[TaskRef]]:
    """(exact, partial) work packages a sentence mentions."""
    words = parsing.tokens(sentence)
    lower = sentence.lower()
    exact: list[TaskRef] = []
    partial: list[TaskRef] = []
    for task in tasks:
        name = parsing.distinctive(task.name)
        if (
            parsing.code_in(task.code, sentence)
            or task.name.lower() in lower
            or (len(name) >= 2 and name <= words)
        ):
            exact.append(task)
        elif name & words & rare:
            partial.append(task)
    # A package whose whole name matched beats ones that only share a word.
    return exact, ([] if exact else partial)


def _duration(
    sentence: parsing.Sentence, context: ChangeContext, ids: _Ids, rare: set[str]
) -> list[DetectedChange]:
    """A named work package with a span ("slips two weeks") or "done" → its new duration.

    "slips / more / late" add the span, "earlier / less" subtract it, otherwise the span is
    the new total. A package only partly named (a rare word such as "SAP") is not guessed:
    the change carries candidates and a question asks which one.
    """
    if parsing.has(sentence.body, _ON_TRACK):
        return []
    exact, partial = match_tasks(sentence.text, context.tasks, rare)
    hits = exact or partial
    if not hits:
        return []
    spans = parsing.spans_days(sentence.body)
    done = not spans and parsing.has(sentence.body, _DONE)
    if not spans and not done:
        return []

    def new_value(task: TaskRef) -> int | None:
        if done:
            return 0
        number = spans[0]
        if parsing.has(sentence.body, _EARLIER):
            return None if task.duration_days is None else max(task.duration_days - number, 1)
        if parsing.has(sentence.body, _LATER):
            return None if task.duration_days is None else task.duration_days + number
        return number

    options = [
        TargetOption(id=task.id, label=_task_label(task), from_=task.duration_days, to=value)
        for task in hits
        if (value := new_value(task)) is not None and value != task.duration_days
    ]
    if not options:
        return []
    confidence = 0.9 if len(exact) == 1 else 0.5
    if len(exact) == 1:
        option = options[0]
        return [
            DetectedChange(
                id=ids.next(),
                kind="duration",
                target=option.id,
                target_label=option.label,
                from_=option.from_,
                to=option.to,
                quote=sentence.body,
                speaker=sentence.speaker,
                confidence=confidence,
                note="Done. Nothing left to plan." if done else None,
            )
        ]
    return [
        DetectedChange(
            id=ids.next(),
            kind="duration",
            to=0 if done else None,
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=confidence,
            candidates=options,
            note="Done. Nothing left to plan." if done else None,
        )
    ]


def _task_label(task: TaskRef) -> str:
    return f"{task.code} {task.name}"


# --------------------------------------------------------------------------- people
def match_people(sentence: str, people: tuple[PersonRef, ...]) -> list[PersonRef]:
    """People a sentence names: full name, or a first / last name unique on the team."""
    words = set(re.findall(r"[a-z0-9']+", sentence.lower()))
    lower = sentence.lower()
    full_matches = [person for person in people if person.name.lower() in lower]
    if full_matches:
        return full_matches
    firsts = Counter(parsing.first_name(person.name).lower() for person in people)
    lasts = Counter(
        person.name.split()[-1].lower() for person in people if len(person.name.split()) > 1
    )
    out = []
    for person in people:
        first = parsing.first_name(person.name).lower()
        last = person.name.split()[-1].lower() if len(person.name.split()) > 1 else None
        if (first in words and firsts[first] == 1) or (
            last is not None and last in words and lasts[last] == 1
        ):
            out.append(person)
    return out


def _capacity(
    sentence: parsing.Sentence, context: ChangeContext, ids: _Ids
) -> list[DetectedChange]:
    """A person with hours a week, or who leaves / is unavailable → their weekly hours
    (0 = off the project). A limited absence is noted: a re-plan can only remove them."""
    people = match_people(sentence.body, context.people)
    if len(people) != 1:
        return []
    person = people[0]
    leaves = parsing.has(sentence.body, _LEAVES)
    hour_figures = parsing.hours(sentence.body)
    if leaves:
        new: float = 0
    elif hour_figures:
        new = hour_figures[0]
    else:
        return []
    if math.isclose(new, person.hours_per_week):
        return []
    period = leaves and bool(
        parsing.spans_days(sentence.body) or parsing.dates(sentence.body, context.today.year)
    )
    return [
        DetectedChange(
            id=ids.next(),
            kind="capacity",
            target=person.id,
            target_label=person.name,
            from_=person.hours_per_week,
            to=new,
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=0.6 if period else 0.85,
            note=(
                "The notes mention a limited period. A re-plan can only take them off the "
                "whole project."
                if period
                else None
            ),
        )
    ]
