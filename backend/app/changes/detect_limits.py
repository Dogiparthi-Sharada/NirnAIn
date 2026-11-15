"""Rules reading of a new budget or deadline in meeting notes (step 7, no AI key).

Purpose: Values must come only from what the notes decide (QA pack M02/M03): actual costs, rates,
the reserve, fixed costs and replaced values are not a new budget, and a "must hold" deadline is
re-planned as hard. Money in a budget sentence becomes a budget change, a date next to a deadline
word a deadline change, and "go-live slips two weeks" moves the deadline by that span.
How it fits: `app.changes.detect.detect` calls these per sentence; questions then confirm each value
and derive.py maps the answer and its mode to the re-plan overrides; layer: changes.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta

from app.changes import parsing, statements
from app.changes.context import ChangeContext
from app.domain.changes import ChangeKind, ChangeMode, DetectedChange

BUDGET_WORDS = re.compile(r"\b(budget|funding|funds|cap|ceiling|financ)", re.I)
DEADLINE_WORDS = re.compile(
    r"\b(go[- ]?live|deadline|launch|cut[- ]?over|release|due|finish(?:es|ed)? by|end date"
    r"|complete[ds]? by|deliver(?:ed|y)? by|wrap(?:s|ped)? up by|ship(?:s|ped)? by|target date)",
    re.I,
)
# A span that moves a date or a duration out ("slips", "two more weeks") or in.
LATER = re.compile(
    r"\b(slip(?:s|ped|ping)?|delay(?:s|ed)?|late|later|behind|push(?:es|ed)?(?: back| out)?"
    r"|extend(?:s|ed)?|move[ds]? (?:out|back)|longer|more|extra|additional|another|overrun"
    r"|add(?:s|ed)?)\b",
    re.I,
)
EARLIER = re.compile(
    r"\b(early|earlier|ahead|faster|shorter|less|fewer|sooner|saves?|saved|pull(?:s|ed)? in)\b",
    re.I,
)

# Hands out the next change id (c1, c2…) when a change is kept.
NextId = Callable[[], str]


def budget(
    sentence: parsing.Sentence, context: ChangeContext, next_id: NextId
) -> list[DetectedChange]:
    """A money amount in a sentence about the budget → a budget change (confirmed through a
    question). Spend, rates, the reserve and replaced amounts are not budgets."""
    if not parsing.has(sentence.text, BUDGET_WORDS):
        return []
    text = statements.without_replaced(sentence.body)
    found = [amount for amount in parsing.amounts(text) if not statements.is_spend(text, amount)]
    if not found:
        return []
    # Several amounts in one sentence: the last one is the new value ("from $X to $Y").
    chosen = found[-1]
    if chosen.cents == context.budget_cents:
        return []
    previous = found[-2].end if len(found) > 1 else 0
    return [
        DetectedChange(
            id=next_id(),
            kind="budget",
            from_=context.budget_cents,
            to=chosen.cents,
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=0.9,
            mode=statements.policy_near(
                text, chosen.start, chosen.end, previous, len(text), DEADLINE_WORDS
            ),
        )
    ]


def deadline(
    sentence: parsing.Sentence, context: ChangeContext, next_id: NextId, year: int
) -> list[DetectedChange]:
    """A date counts as a new deadline only next to a deadline word (a meeting date doesn't);
    a replaced date ("this replaces the July 30 target") is the old one."""
    if not parsing.has(sentence.text, DEADLINE_WORDS):
        return []
    text = statements.without_replaced(sentence.body)
    found = parsing.date_spans(text, year)
    if not found:
        return []
    chosen = found[-1]
    if chosen.value == context.deadline:
        return []
    previous = found[-2].end if len(found) > 1 else 0
    return [
        DetectedChange(
            id=next_id(),
            kind="deadline",
            from_=context.deadline.isoformat() if context.deadline else None,
            to=chosen.value.isoformat(),
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=0.85,
            mode=statements.policy_near(
                text, chosen.start, chosen.end, previous, len(text), BUDGET_WORDS
            ),
        )
    ]


def deadline_shift(
    sentence: parsing.Sentence, context: ChangeContext, next_id: NextId
) -> list[DetectedChange]:
    """ "Go-live slips two weeks": the deadline moves by a stated span."""
    if context.deadline is None or not parsing.has(sentence.text, DEADLINE_WORDS):
        return []
    spans = parsing.spans_days(sentence.body)
    if not spans:
        return []
    sign = (
        -1 if parsing.has(sentence.body, EARLIER) else 1 if parsing.has(sentence.body, LATER) else 0
    )
    if sign == 0:
        return []
    new = _add_workdays(context.deadline, sign * spans[0])
    return [
        DetectedChange(
            id=next_id(),
            kind="deadline",
            from_=context.deadline.isoformat(),
            to=new.isoformat(),
            quote=sentence.body,
            speaker=sentence.speaker,
            confidence=0.7,
            mode=statements.policy(sentence.body),
        )
    ]


def stated_policy(sentence: parsing.Sentence, kind: ChangeKind) -> ChangeMode | None:
    """The policy a sentence states for the budget or the deadline without a new value
    ("This replaces the July 30 target and is a hard deadline")."""
    words = BUDGET_WORDS if kind == "budget" else DEADLINE_WORDS
    if not parsing.has(sentence.text, words):
        return None
    return statements.policy(sentence.body)


def _add_workdays(current: date, days: int) -> date:
    """Return the date `days` working days (Mon-Fri) after `current`, or before it when negative."""
    step = 1 if days >= 0 else -1
    left = abs(days)
    while left:
        current += timedelta(days=step)
        if current.weekday() < 5:
            left -= 1
    return current
