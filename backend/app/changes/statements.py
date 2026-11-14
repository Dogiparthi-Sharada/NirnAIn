"""What a sentence of meeting notes says about its own values (step 7, rules reader).

What: whether a sentence rejects what it mentions ("we discussed $400,000 but rejected it"),
which values it replaces ("24 hours instead of 40"), the policy it states for a budget or a
deadline ("must hold" = hard, "may flex" = soft), and money that is spend rather than a
budget (actual costs, hourly rates, the reserve, fixed costs).
Why: the deterministic reader must never propose a value the notes don't decide (QA pack
M02/M03: rejected proposals, actual costs and rates were offered as the new budget), and a
"must hold" next to a new date must reach the re-plan as a hard deadline.
How it fits: `app.changes.detect`, `app.changes.detect_limits` and
`app.changes.detect_people` call these per sentence. Pure functions, no I/O.
"""

from __future__ import annotations

import re

from app.changes import parsing
from app.domain.changes import ChangeMode

# A sentence that turns something down proposes none of its values.
_REJECTED = re.compile(
    r"\b(reject(?:s|ed|ing)?|declin(?:e|es|ed|ing)|not approved|turned down|ruled out|vetoed)\b",
    re.I,
)
# "instead of $375,000", "rather than 80", "this replaces the July 30 target": the value
# after it is the old one.
_REPLACED = re.compile(
    r"\b(?:instead of|rather than|replac(?:e|es|ing))\s+(?:the\s+)?(?:(?:old|previous|current)\s+)?"
    r"(?:\$\s?[\d,.]+\s?(?:k|m|million|thousand)?|\d{4}-\d{2}-\d{2}"
    rf"|{parsing.MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
    r"|[\d,.]+(?:\s*(?:hours?|h|days?|weeks?))?)",
    re.I,
)
_HARD = re.compile(
    r"\b(hard|must hold|fixed|firm|cannot (?:move|slip|change|be (?:moved|exceeded|changed))"
    r"|can'?t (?:move|slip|change)|won'?t move|not negotiable|non-negotiable)\b",
    re.I,
)
_SOFT = re.compile(r"\b(soft|(?:may|can) flex|flexible)\b", re.I)
# Money next to these words is spend, a rate or a set-aside, never the budget.
_SPEND_BEFORE = re.compile(
    r"\b(actual|spent|to date|so far|rate|reserved?|contingency|fixed (?:costs?|infrastructure)"
    r"|infrastructure)\b",
    re.I,
)
_SPEND_AFTER = re.compile(
    r"^\s*(?:(?:actual |labou?r |in )?costs?\b|actual\b|per hour|an hour|/\s?h(?:r|our)?\b"
    r"|hourly|(?:is |are |was |were )?(?:reserved|spent)\b|so far|to date)",
    re.I,
)
# How far around an amount its label is read.
LABEL_CHARS = 30


def rejects(text: str) -> bool:
    """Whether the sentence rejects or declines what it mentions."""
    return parsing.has(text, _REJECTED)


def without_replaced(text: str) -> str:
    """The sentence with each replaced value ("instead of 40") blanked out, same length, so
    positions in it still match the original sentence."""
    return _REPLACED.sub(lambda match: " " * len(match.group()), text)


def policy(text: str) -> ChangeMode | None:
    """The policy a phrase states: "hard" (must hold, fixed…), "soft" (may flex…) or None."""
    if parsing.has(text, _HARD):
        return "hard"
    if parsing.has(text, _SOFT):
        return "soft"
    return None


def policy_near(
    text: str, start: int, end: int, previous: int, following: int, other: re.Pattern[str]
) -> ChangeMode | None:
    """The policy stated for the value at `start` to `end`: the words after it (up to the next
    value at `following`) come first ("$200,000 hard budget"), then the words before it back
    to the previous value at `previous` ("the budget is fixed at $300,000"). Words about the
    `other` limit ("…is a hard deadline and the budget…") belong to that one."""
    after = text[end:following]
    cut = other.search(after)
    if cut is not None:
        after = after[: cut.start()]
    before = text[previous:start]
    for mention in other.finditer(before):
        before = text[previous + mention.end() : start]
    return policy(after) or policy(before)


def is_spend(text: str, amount: parsing.Amount) -> bool:
    """Money labelled as actual spend, a cost, a rate, the reserve or a fixed cost."""
    before = re.split(r"[,;:]|\band\b", text[max(amount.start - LABEL_CHARS, 0) : amount.start])
    after = text[amount.end : amount.end + LABEL_CHARS]
    return parsing.has(before[-1], _SPEND_BEFORE) or parsing.has(after, _SPEND_AFTER)
