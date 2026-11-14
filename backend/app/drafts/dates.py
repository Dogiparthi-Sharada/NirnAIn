"""Date mentions for the rules reader: exact dates, quarters and months, and what they are for.

Purpose: Pick a start and a target end date only from dates the text gives a role: "Kickoff on
2025-03-03" is a start, "go-live by Sep 30, 2025" an end, "by Q3 2025" an end on the quarter's last
day; a date with no start or deadline word next to it is ignored, never guessed.
How it fits: Used by `app.drafts.rules.find_dates`; pure; layer: drafts.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

Role = Literal["start", "end"]

# How far after a date a cue word may appear ("January 15, 2025 kickoff").
ROLE_AFTER_CHARS = 40

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

_ISO_DATE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")
_MONTH_DAY_YEAR = re.compile(
    rf"\b(?P<month_name>{_MONTH})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>\d{{4}})\b",
    re.IGNORECASE,
)
_DAY_MONTH_YEAR = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month_name>{_MONTH})\.?,?\s+(?P<year>\d{{4}})\b",
    re.IGNORECASE,
)
_QUARTER = re.compile(r"\bQ(?P<quarter>[1-4])\s*(?:FY\s*)?(?P<year>\d{4})\b", re.IGNORECASE)
_MONTH_YEAR = re.compile(rf"\b(?P<month_name>{_MONTH})\.?\s+(?P<year>\d{{4}})\b", re.IGNORECASE)

_DEADLINE_BEFORE = re.compile(
    r"\b(?:by|until|before|no\s+later\s+than|not\s+later\s+than)\s+(?:the\s+)?(?:end\s+of\s+)?$",
    re.IGNORECASE,
)
_ROLE_WORDS = re.compile(
    r"(?P<start>\bstart\w*|\bkick[\s-]?off\w*|\bbegin\w*|\bcommenc\w*)"
    r"|(?P<end>\bgo[\s-]?live\b|\blaunch\w*|\bdeadline\w*|\bdue\b|\bdeliver\w*|\bfinish\w*"
    r"|\bcomplet\w*|\bend(?:s|ing)?\b(?!-)|\btarget\w*|\bwrap\w*\s+up)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DateHit:
    """A date mention in a sentence; a quarter or month spans `first`..`last`."""

    start: int
    end: int
    first: date
    last: date
    exact: bool


def _month(name: str) -> int:
    """Return the month number (1..12) of an English month name or abbreviation."""
    return _MONTHS.index(name[:3].lower()) + 1


def date_hits(sentence: str) -> list[DateHit]:
    """Every date mention in `sentence`, in order; precise dates win over overlapping months."""
    hits: list[DateHit] = []

    def is_free(match: re.Match[str]) -> bool:
        """Not inside a more precise date found earlier ("15 January 2025" vs "January 2025")."""
        return all(match.end() <= hit.start or match.start() >= hit.end for hit in hits)

    for pattern in (_ISO_DATE, _MONTH_DAY_YEAR, _DAY_MONTH_YEAR):
        for match in pattern.finditer(sentence):
            if not is_free(match):
                continue
            month = (
                int(match["month"])
                if "month" in pattern.groupindex
                else _month(match["month_name"])
            )
            try:
                day = date(int(match["year"]), month, int(match["day"]))
            except ValueError:
                continue
            hits.append(DateHit(match.start(), match.end(), day, day, exact=True))
    for match in _QUARTER.finditer(sentence):
        if is_free(match):
            year, quarter = int(match["year"]), int(match["quarter"])
            first = date(year, 3 * quarter - 2, 1)
            last_month = 3 * quarter
            last = date(year, last_month, calendar.monthrange(year, last_month)[1])
            hits.append(DateHit(match.start(), match.end(), first, last, exact=False))
    for match in _MONTH_YEAR.finditer(sentence):
        if is_free(match):
            year, month = int(match["year"]), _month(match["month_name"])
            last = date(year, month, calendar.monthrange(year, month)[1])
            hits.append(
                DateHit(match.start(), match.end(), date(year, month, 1), last, exact=False)
            )
    return sorted(hits, key=lambda hit: hit.start)


def date_role(before: str, after: str) -> Role | None:
    """What a date is for, from the nearest cue word before it (else just after it)."""
    if _DEADLINE_BEFORE.search(before):
        return "end"
    cues = list(_ROLE_WORDS.finditer(before))
    if cues:
        return "start" if cues[-1]["start"] else "end"
    cue = _ROLE_WORDS.search(after[:ROLE_AFTER_CHARS])
    if cue:
        return "start" if cue["start"] else "end"
    return None


def strip_trailing_date(text: str, separators: str) -> str:
    """`text` without a date at its very end (and the `separators` before it)."""
    for pattern in (_ISO_DATE, _MONTH_DAY_YEAR, _DAY_MONTH_YEAR, _QUARTER, _MONTH_YEAR):
        found = [match for match in pattern.finditer(text) if not text[match.end() :].strip(" )")]
        if found:
            return text[: found[-1].start()].rstrip(separators)
    return text
