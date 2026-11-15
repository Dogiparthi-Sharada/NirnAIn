"""Keyword rules of the rule-based intake extractor (used when Claude is unavailable).

Purpose: Classify a sentence predictably by the first category whose keywords it contains, most
specific signals first: an open question about the budget is missing information, a risk to the
launch date is a risk, "must go live by Q3" is a constraint. Sentences matching nothing are not
scope items.
How it fits: Used by intake_sentences.py and intake_rules.py; pure; layer: ai.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import re
from collections.abc import Callable

from app.domain.common import ScopeCategory

_MONTH = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_WEEKDAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_PERIOD = r"week|month|quarter|year|sprint"
_SMALL_NUMBER = r"\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|twenty"
_APOSTROPHE = "['\u2019]"
# What may follow "by" / "before" / "until" in a deadline: "Q3", "March", "the 15th",
# "12/31", "2026", "Friday", "year-end", "next month", "the end of the quarter".
_DATE = (
    rf"(?:{_MONTH}|q[1-4]|h[12]|\d{{1,2}}[/.-]\d{{1,2}}(?:[/.-]\d{{2,4}})?|(?:19|20)\d{{2}}|"
    rf"\d{{1,2}}(?:st|nd|rd|th)|{_WEEKDAY}|year[- ]end|(?:this|next|the) (?:{_PERIOD}))"
)

# Budgets and amounts: "$375K", "€2m", "375K", "1.5 million", "90,000 dollars", "the budget".
MONEY_MENTION = re.compile(
    r"[$€£]\s?\d|\b\d[\d,.]*(?:k|m|bn)\b|\b\d[\d,.]*\s?(?:million|thousand|billion)\b|"
    r"\b\d[\d,.]*\s?(?:usd|eur|gbp|dollars?|euros?|pounds?)\b|\bbudget",
    re.IGNORECASE,
)
# Deadlines: "deadline", "go live by", "by Q3", "before March", "by the end of the quarter".
DEADLINE_MENTION = re.compile(
    r"\b(?:deadlines?|due (?:by|on|date)|go[- ]?live (?:by|in|on|before|date)|no later than|"
    rf"(?:by|before|until) (?:the )?(?:(?:end|middle|start) of (?:the )?|mid[- ]?|early |late )?"
    rf"{_DATE})\b",
    re.IGNORECASE,
)
# Hard limits: "only 3 developers", "we only have two testers", "limited to two vendors".
LIMIT_MENTION = re.compile(
    rf"\b(?:only (?:(?:have|has|had|get|got) )?(?:{_SMALL_NUMBER})|limited to|no more than|"
    r"at most|capped at|maximum of)\b",
    re.IGNORECASE,
)

MISSING_INFO_WORDS = re.compile(
    rf"\b(?:don{_APOSTROPHE}?t know|do not know|not sure|tbd|tbc|unclear|not (?:yet )?defined|"
    r"undefined|unknown|need to (?:find out|check|confirm|figure out)|"
    r"to be (?:decided|confirmed|determined))\b",
    re.IGNORECASE,
)
RISK_WORDS = re.compile(
    r"\b(?:risks?|risky|concerns?|concerned|worr(?:y|ied|ies)|(?:might|could|may) (?:delay|slip)|"
    r"issues?|dependenc(?:y|ies)|depends on|blockers?|blocked)\b",
    re.IGNORECASE,
)
OBJECTIVE_WORDS = re.compile(
    r"\b(?:goals?|objectives?|aims?|aimed|aiming|we want to|purpose)\b", re.IGNORECASE
)
DELIVERABLE_WORDS = re.compile(
    r"\b(?:deliver(?:s|ed|ing|ables?)?|build(?:s|ing)?|launch(?:es|ed|ing)?|"
    r"roll(?:s|ing)? out|produce[sd]?|migrat(?:e|es|ed|ing|ion))\b",
    re.IGNORECASE,
)
REQUIREMENT_WORDS = re.compile(
    r"\b(?:must|needs? to|should|requir(?:e|es|ed|ements?)|"
    r"(?:has|have) to (?:support|be|handle|include|integrate|comply|work))\b",
    re.IGNORECASE,
)
# Meeting chatter phrased as a question is not an open item ("Can you see my screen?").
SMALL_TALK = re.compile(
    r"\b(?:hear me|see my screen|share my screen|on mute|muted|make sense|how are you|"
    r"how(?:'s| is) (?:it going|everyone)|sound good|any (?:other )?questions|"
    r"anything else|everyone here|ready to start|shall we start)\b",
    re.IGNORECASE,
)
_CLOSING_PUNCTUATION = "\"')\u201d\u2019"


def mentions_budget_or_deadline(text: str) -> bool:
    """A budget / money amount or a deadline (the bullet path's extra constraint item)."""
    return bool(MONEY_MENTION.search(text) or DEADLINE_MENTION.search(text))


def mentions_constraint(text: str) -> bool:
    """A budget / amount, a deadline or a hard limit."""
    return mentions_budget_or_deadline(text) or bool(LIMIT_MENTION.search(text))


def _is_open_question(text: str) -> bool:
    """A question that asks for project information (not meeting chatter)."""
    is_question = text.rstrip().rstrip(_CLOSING_PUNCTUATION).endswith("?")
    return is_question and not SMALL_TALK.search(text)


def _asks_for_information(text: str) -> bool:
    """Tell whether the sentence asks for or flags missing project information."""
    return _is_open_question(text) or bool(MISSING_INFO_WORDS.search(text))


def _names_risk(text: str) -> bool:
    """Tell whether the sentence names a risk."""
    return bool(RISK_WORDS.search(text))


def _states_objective(text: str) -> bool:
    """Tell whether the sentence states an objective."""
    return bool(OBJECTIVE_WORDS.search(text))


def _names_deliverable(text: str) -> bool:
    """Tell whether the sentence names a deliverable."""
    return bool(DELIVERABLE_WORDS.search(text))


def _states_requirement(text: str) -> bool:
    """Tell whether the sentence states a requirement."""
    return bool(REQUIREMENT_WORDS.search(text))


# Checked in this order; the first rule that matches decides the category.
CATEGORY_RULES: tuple[tuple[ScopeCategory, Callable[[str], bool]], ...] = (
    (ScopeCategory.MISSING_INFO, _asks_for_information),
    (ScopeCategory.RISK, _names_risk),
    (ScopeCategory.CONSTRAINT, mentions_constraint),
    (ScopeCategory.OBJECTIVE, _states_objective),
    (ScopeCategory.DELIVERABLE, _names_deliverable),
    (ScopeCategory.REQUIREMENT, _states_requirement),
)


def classify(text: str) -> ScopeCategory | None:
    """The category of the first rule in `CATEGORY_RULES` that matches; None if none does."""
    return next((category for category, matches in CATEGORY_RULES if matches(text)), None)
