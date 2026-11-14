"""Deterministic "Find what changed" (no AI key configured, or as the fallback).

Purpose: Report only what a sentence states and never invent values: budget and deadline (with their
stated policy), durations or "done", hours or leaving, specialists brought in and new scope. A
sentence that rejects something proposes nothing, a value after "instead of" is the old one, and
anything it can't pin down becomes a question.
How it fits: Called by `ChangeDetector` (app/changes/llm.py); budgets and deadlines are read by
detect_limits.py, people by detect_people.py; layer: changes.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import re
import uuid
from collections import Counter

from app.changes import detect_limits, parsing, statements
from app.changes.context import ChangeContext, TaskRef
from app.changes.detect_people import capacity_change, newcomer_change
from app.domain.changes import ChangeAction, ChangeKind, DetectedChange, TargetOption

_DONE = re.compile(r"\b(done|complete|completed|finished|delivered|wrapped up)\b", re.I)
# "no completed work", "isn't finished", "will be done": not done (yet).
_NOT_YET = re.compile(
    r"(?:\b(?:no|not|never)|n't|\b(?:will|must|should|could|would|can|to|needs?|has|have)"
    r"(?: to)? be)\s+(?:yet\s+|fully\s+|been\s+)?$",
    re.I,
)
# "completed W01", "finished the Salesforce connector": the verb before the package.
_DONE_VERB_BEFORE = re.compile(
    r"\b(?:completed|finished|delivered|wrapped up)\s+(?:the\s+|our\s+)?$", re.I
)
# "W12 has been completed": at most this many words between the package and "done".
MAX_DONE_GAP_WORDS = 4
# "W05 starts once the sandbox is done": the done word belongs to something else.
_CONDITION = re.compile(r"\b(once|when|after|before|until|if|unless)\b", re.I)
_IDENTIFIER = re.compile(r"[a-z]+\d+[a-z\d]*")
_SCOPE = re.compile(
    r"\b((?:has|have|needs?|must) to be added|must be added|needs? adding|add(?:s|ed|ing)? .{0,60}"
    r"\b(?:as a|to the scope|requirement)|new requirement|also (?:need|want|require)s?"
    r"|must (?:include|support)|(?:drop|remove|descope|cut)(?:s|ped|d)? .{0,40}\bscope)",
    re.I,
)
_DESCOPE = re.compile(
    r"\b(?:drop|remove|descope|cut)(?:s|ped|d)?\b.{0,40}\bscope|\bdescop|\bout of scope\b", re.I
)
_ON_TRACK = re.compile(r"\bon (track|schedule)\b", re.I)
_LIMITS: tuple[ChangeKind, ...] = ("budget", "deadline")


class _Ids:
    """Sequential change ids (c1, c2…) that questions and answers refer to."""

    def __init__(self) -> None:
        """Start numbering at c1."""
        self.count = 0

    def next(self) -> str:
        """Take the next id."""
        self.count += 1
        return f"c{self.count}"

    def peek(self) -> str:
        """The id `next` will hand out, for a change that may not be kept."""
        return f"c{self.count + 1}"


def detect(text: str, context: ChangeContext) -> list[DetectedChange]:
    """Changes the notes state, in reading order, each with its verbatim quote.

    Pure and deterministic: used when no AI key is configured and when the Claude call
    fails. Values come only from the sentence and the project (never invented).
    """
    ids = _Ids()
    year = (context.deadline or context.plan_finish or context.today).year
    rare = _rare_tokens(context.tasks)
    read: list[tuple[parsing.Sentence, list[DetectedChange]]] = []
    for sentence in parsing.sentences(text):
        # Business rule: a rejected or declined proposal is not a change.
        if statements.rejects(sentence.body):
            read.append((sentence, []))
            continue
        found: list[DetectedChange] = []
        found += detect_limits.budget(sentence, context, ids.next)
        found += detect_limits.deadline(sentence, context, ids.next, year)
        capacity = capacity_change(sentence, context, ids.peek())
        if capacity is not None:
            ids.next()
            found.append(capacity)
        else:
            found += _duration(sentence, context, ids, rare)
        newcomer = newcomer_change(sentence, context, ids.peek(), hours_taken=capacity is not None)
        if newcomer is not None:
            ids.next()
            found.append(newcomer)
        if not any(change.kind in ("duration", "deadline") for change in found):
            found += detect_limits.deadline_shift(sentence, context, ids.next)
        if not found and parsing.has(sentence.body, _SCOPE):
            action, note = scope_action(sentence.body)
            found.append(
                DetectedChange(
                    id=ids.next(),
                    kind="scope",
                    action=action,
                    to=sentence.body,
                    quote=sentence.body,
                    speaker=sentence.speaker,
                    confidence=0.7,
                    note=note,
                )
            )
        read.append((sentence, found))
    _apply_stated_policies(read)
    return _numbered(_without_repeats([change for _, found in read for change in found]))


def _apply_stated_policies(read: list[tuple[parsing.Sentence, list[DetectedChange]]]) -> None:
    """A sentence that only states a policy ("This replaces the July 30 target and is a hard
    deadline") gives it to the new budget / deadline of the sentence before (or after) it."""
    for index, (sentence, found) in enumerate(read):
        for kind in _LIMITS:
            if any(change.kind == kind for change in found) or statements.rejects(sentence.body):
                continue
            mode = detect_limits.stated_policy(sentence, kind)
            if mode is None:
                continue
            for neighbour in (index - 1, index + 1):
                if not 0 <= neighbour < len(read):
                    continue
                changes = read[neighbour][1]
                open_changes = [
                    position
                    for position, change in enumerate(changes)
                    if change.kind == kind and change.mode is None
                ]
                for position in open_changes:
                    changes[position] = changes[position].model_copy(update={"mode": mode})
                if open_changes:
                    break


def _without_repeats(changes: list[DetectedChange]) -> list[DetectedChange]:
    """A work package or person the notes give the same value twice ("W09 … finished
    January 29. W09 and W12 are done.") is one change, quoted where it is first said."""
    seen: set[tuple[str, uuid.UUID, str]] = set()
    kept: list[DetectedChange] = []
    for change in changes:
        if change.target is not None and change.kind in ("duration", "capacity"):
            key = (change.kind, change.target, str(change.to))
            if key in seen:
                continue
            seen.add(key)
        kept.append(change)
    return kept


def _numbered(changes: list[DetectedChange]) -> list[DetectedChange]:
    """Ids c1, c2… in reading order (questions and answers refer to them)."""
    return [
        change.model_copy(update={"id": f"c{number}"})
        for number, change in enumerate(changes, start=1)
    ]


def scope_action(text: str) -> tuple[ChangeAction, str]:
    """Action and note of a requirement change. Re-planning can't turn text into work: a
    new requirement joins the project's scope on approval; a removal is only noted."""
    if parsing.has(text, _DESCOPE):
        return (
            "remove",
            "Scope reduction noted. Re-planning plans the work in the WBS: remove its work "
            "packages in WBS Builder (step 4), or mark them done in a progress update.",
        )
    return (
        "add",
        "New requirement noted. It joins the project's scope when you approve. Re-planning "
        "plans the work in the WBS: add a work package for it in WBS Builder (step 4) "
        "before you re-plan, or give an existing one more time.",
    )


# --------------------------------------------------------------------------- work packages
def _rare_tokens(tasks: tuple[TaskRef, ...]) -> set[str]:
    """Name tokens used by at most two work packages: enough to suggest a match."""
    counts: Counter[str] = Counter()
    for task in tasks:
        counts.update(parsing.distinctive(task.name))
    return {token for token, uses in counts.items() if uses <= 2 and len(token) > 2}


def _identifiers(tasks: tuple[TaskRef, ...]) -> dict[str, TaskRef]:
    """Id-like name tokens ("W09") that belong to exactly one work package."""
    owners: dict[str, list[TaskRef]] = {}
    for task in tasks:
        for token in parsing.distinctive(task.name):
            if _IDENTIFIER.fullmatch(token):
                owners.setdefault(token, []).append(task)
    return {token: found[0] for token, found in owners.items() if len(found) == 1}


def match_tasks(
    sentence: str, tasks: tuple[TaskRef, ...], rare: set[str]
) -> tuple[list[TaskRef], list[TaskRef]]:
    """(exact, partial) work packages a sentence mentions. Exact = named explicitly: its WBS
    code, its whole name, all of its distinctive words (two or more, id aside), or its own
    id ("W09")."""
    words = parsing.tokens(sentence)
    lower = sentence.lower()
    named_by_id = {task.id for token, task in _identifiers(tasks).items() if token in words}
    exact: list[TaskRef] = []
    partial: list[TaskRef] = []
    for task in tasks:
        name = parsing.distinctive(task.name)
        # "the Schema design" names "W04 Schema design": its id is not needed as well.
        core = {token for token in name if not _IDENTIFIER.fullmatch(token)}
        if (
            parsing.code_in(task.code, sentence)
            or task.name.lower() in lower
            or (len(name) >= 2 and name <= words)
            or (len(core) >= 2 and core <= words)
            or task.id in named_by_id
        ):
            exact.append(task)
        elif name & words & rare:
            partial.append(task)
    # A package whose whole name matched beats ones that only share a word.
    return exact, ([] if exact else partial)


def _mentions(task: TaskRef, text: str) -> list[re.Match[str]]:
    """Where a sentence names the work package (its code, name or a distinctive word)."""
    words = [re.escape(task.code), re.escape(task.name)] + [
        rf"{re.escape(token)}s?" for token in parsing.distinctive(task.name)
    ]
    return list(re.finditer(rf"(?<![\w.])(?:{'|'.join(words)})(?![\w])", text, re.I))


def says_done(task: TaskRef, text: str) -> bool:
    """Business rule: work is done only when the notes say so of this package: "W01
    completed on January 15", "W09 and W12 are done", "we finished the Salesforce
    connector". Not "no completed work", "will be done", "complete by June" or "starts once
    the sandbox is done"."""
    mentions = _mentions(task, text)
    if not mentions:
        return False
    first, last = mentions[0].start(), mentions[-1].end()
    if parsing.has(re.split(r"[,;:]", text[:first])[-1], _CONDITION):
        return False
    for word in _DONE.finditer(text):
        if parsing.has(text[: word.start()], _NOT_YET) or re.match(r"\s+by\b", text[word.end() :]):
            continue
        gap = text[last : word.start()]
        close = len(gap.split()) <= MAX_DONE_GAP_WORDS and not parsing.has(gap, _CONDITION)
        if word.start() >= last and close:
            return True
        if word.end() <= first and parsing.has(text[:first], _DONE_VERB_BEFORE):
            return True
    return False


def _duration(
    sentence: parsing.Sentence, context: ChangeContext, ids: _Ids, rare: set[str]
) -> list[DetectedChange]:
    """A named work package with a span ("slips two weeks") → its new duration; an
    explicitly named package that is done → 0 days left (one change per package).

    "slips / more / late" add the span, "earlier / less" subtract it, otherwise the span is
    the new total. A package only partly named (a rare word such as "SAP") is not guessed:
    the change carries candidates and a question asks which one.
    """
    if parsing.has(sentence.body, _ON_TRACK):
        return []
    exact, partial = match_tasks(sentence.text, context.tasks, rare)
    spans = parsing.spans_days(sentence.body)
    if not spans:
        return [
            _change(sentence, ids, task, 0)
            for task in exact
            if task.duration_days != 0 and says_done(task, sentence.text)
        ]
    hits = exact or partial

    def new_value(task: TaskRef) -> int | None:
        """Return the package's new duration (earlier/later by a span, or a new total)."""
        number = spans[0]
        if parsing.has(sentence.body, detect_limits.EARLIER):
            return None if task.duration_days is None else max(task.duration_days - number, 1)
        if parsing.has(sentence.body, detect_limits.LATER):
            return None if task.duration_days is None else task.duration_days + number
        return number

    changed = [
        (task, value)
        for task in hits
        if (value := new_value(task)) is not None and value != task.duration_days
    ]
    if len(exact) == 1 and changed:
        task, value = changed[0]
        return [_change(sentence, ids, task, value)]
    if not changed:
        return []
    return [
        DetectedChange(
            id=ids.next(),
            kind="duration",
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=0.5,
            candidates=[
                TargetOption(
                    id=task.id, label=_task_label(task), from_=task.duration_days, to=value
                )
                for task, value in changed
            ],
        )
    ]


def _change(sentence: parsing.Sentence, ids: _Ids, task: TaskRef, days: int) -> DetectedChange:
    """A work package's new duration (0 = done, nothing left to plan)."""
    return DetectedChange(
        id=ids.next(),
        kind="duration",
        target=task.id,
        target_label=_task_label(task),
        from_=task.duration_days,
        to=days,
        quote=sentence.body,
        speaker=sentence.speaker,
        confidence=0.9,
        note="Done. Nothing left to plan." if days == 0 else None,
    )


def _task_label(task: TaskRef) -> str:
    """Return `code name` of a work package."""
    return f"{task.code} {task.name}"
