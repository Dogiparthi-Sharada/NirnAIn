"""Text helpers for reading meeting notes: sentences, money, dates, durations, hours, names."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.ai.transcripts import clean_transcript

DAYS_PER_WEEK = 5
DAYS_PER_MONTH = 21

_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "a couple of": 2,
    "couple of": 2,
    "a few": 3,
}
_NUMBER = (
    r"\d+(?:\.\d+)?|a couple of|couple of|a few|an?|one|two|three|four|five|six|seven|eight"
    r"|nine|ten|eleven|twelve"
)
_MONTH_KEYS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTHS = {key: number for number, key in enumerate(_MONTH_KEYS, start=1)}
# Month names (also read by app.changes.statements).
MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
# "Robert:", "Jane Doe, project manager:", "Robert Walsh (VP Eng):". A speaker's name is up to
# four capitalised words without digits, so "W04 also completed on January 15:" is not one.
_NAME_WORD = r"[A-Z][A-Za-z'\u2019.-]*"
_SPEAKER = re.compile(
    rf"^(?P<speaker>{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}}(?:\s*\([^()\d]{{1,40}}\))?"
    r"(?:,\s*[A-Za-z][A-Za-z .'&/-]{0,40})?)\s*:\s+(?P<body>\S.*)$",
    re.S,
)
_BULLET = re.compile(r"^[\s\u2022\u2013\u00b7*>-]+")
_MONEY = re.compile(
    r"(?:\$|\bUSD\s?)\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<unit>k|mm|m|million|thousand|bn|billion)?\b",
    re.I,
)
_MONEY_WORDS = re.compile(
    r"\b(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?P<unit>k|mm|m|million|thousand)?"
    r"\s?(?:dollars|usd)\b",
    re.I,
)
_UNITS = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "mm": 1_000_000,
    "million": 1_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
}
_ISO_DATE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_MONTH_DAY_YEAR = re.compile(
    rf"\b(?P<mon>{MONTH})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<y>\d{{4}}))?", re.I
)
_DAY_MONTH_YEAR = re.compile(
    rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{MONTH})\b\.?(?:,?\s+(?P<y>\d{{4}}))?",
    re.I,
)
_SPAN = re.compile(
    rf"\b(?P<count>{_NUMBER})\s*(?:more\s+|extra\s+|additional\s+|further\s+)?"
    r"(?P<unit>weeks?|wks?|(?:working |business |work )?days?|months?)\b",
    re.I,
)
# "16 hours a week", "32 project hours per week", "20h/wk": hours someone gives each week.
# Increments ("16 more hours") and totals of work ("80 actual hours") are not weekly hours.
_WEEKLY_HOURS = re.compile(
    rf"\b(?P<count>{_NUMBER})\s*(?:(?!more\b|extra\b|additional\b|fewer\b|less\b|actual\b"
    r"|total\b|remaining\b|overtime\b)[a-z-]+\s+){0,2}?(?:hours?|hrs?|h)\b"
    r"\s*(?:(?:a|per|/|each|every)\s*(?:week|wk)\b|weekly\b)",
    re.I,
)
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "or",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "with",
        "by",
        "from",
        "as",
        "is",
        "are",
        "be",
        "it",
        "its",
        "this",
        "that",
        "set",
        "up",
        "setup",
        "development",
        "work",
        "task",
        "tasks",
        "package",
        "packages",
        "phase",
    ]
)


@dataclass(frozen=True, slots=True)
class Sentence:
    """A sentence of the notes; `body` is what gets quoted, `speaker` the "Name:" prefix."""

    text: str  # the whole sentence, verbatim
    body: str  # without a leading "Name:" speaker prefix, verbatim
    speaker: str | None


def sentences(text: str) -> list[Sentence]:
    """Split notes into sentences (and lines), dropping bullets and keeping text verbatim.

    Pasted captions (WebVTT / SRT) are first reduced to their spoken lines, with `<v Name>`
    voice tags as "Name: " speakers.
    """
    out: list[Sentence] = []
    for raw in _SPLIT.split(clean_transcript(text)):
        line = _BULLET.sub("", raw).strip()
        if not line:
            continue
        match = _SPEAKER.match(line)
        if match:
            out.append(Sentence(line, match["body"].strip(), match["speaker"].strip()))
        else:
            out.append(Sentence(line, line, None))
    return out


def _number(raw: str) -> float | None:
    key = raw.lower()
    if key in _WORDS:
        return float(_WORDS[key])
    try:
        return float(key)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Amount:
    """A money amount in a sentence: where it is (`start`, `end`) and its value in cents."""

    start: int
    end: int
    cents: int


def amounts(text: str) -> list[Amount]:
    """Money amounts in a sentence, in order of appearance (zero and repeats dropped)."""
    found: list[Amount] = []
    for pattern in (_MONEY, _MONEY_WORDS):
        for match in pattern.finditer(text):
            try:
                value = Decimal(match["num"].replace(",", ""))
            except InvalidOperation:
                continue
            unit = (match["unit"] or "").lower()
            value *= _UNITS.get(unit, 1)
            found.append(Amount(match.start(), match.end(), int(value * 100)))
    found.sort(key=lambda amount: amount.start)
    out: list[Amount] = []
    for amount in found:
        if amount.cents > 0 and all(kept.cents != amount.cents for kept in out):
            out.append(amount)
    return out


@dataclass(frozen=True, slots=True)
class DateSpan:
    """A calendar date in a sentence and where it is (`start`, `end`)."""

    start: int
    end: int
    value: date


def date_spans(text: str, default_year: int) -> list[DateSpan]:
    """Calendar dates in a sentence, in order (repeats dropped). A date without a year
    takes `default_year`."""
    found: list[DateSpan] = []
    for match in _ISO_DATE.finditer(text):
        try:
            value = date(int(match["y"]), int(match["m"]), int(match["d"]))
        except ValueError:
            continue
        found.append(DateSpan(match.start(), match.end(), value))
    for pattern in (_MONTH_DAY_YEAR, _DAY_MONTH_YEAR):
        for match in pattern.finditer(text):
            month = _MONTHS[match["mon"][:3].lower()]
            year = int(match["y"]) if match["y"] else default_year
            try:
                value = date(year, month, int(match["d"]))
            except ValueError:
                continue
            found.append(DateSpan(match.start(), match.end(), value))
    found.sort(key=lambda span: span.start)
    out: list[DateSpan] = []
    for span in found:
        if all(kept.value != span.value for kept in out):
            out.append(span)
    return out


def dates(text: str, default_year: int) -> list[date]:
    """Calendar dates in a sentence. A date without a year takes `default_year`."""
    return [span.value for span in date_spans(text, default_year)]


def spans_days(text: str) -> list[int]:
    """Durations ("two weeks", "3 days", "1 month") as working days."""
    out: list[int] = []
    for match in _SPAN.finditer(text):
        number = _number(match["count"])
        if number is None or number <= 0:
            continue
        unit = match["unit"].lower()
        if unit.startswith("w"):
            days = number * DAYS_PER_WEEK
        elif unit.startswith("month"):
            days = number * DAYS_PER_MONTH
        else:
            days = number
        out.append(round(days))
    return out


def weekly_hours(text: str) -> list[tuple[int, float]]:
    """Hours a week ("20 hours a week", "32 project hours per week", "16h/wk") between 0
    and 80, each with where it starts. Other hour figures (a task's 80 hours) don't count."""
    out: list[tuple[int, float]] = []
    for match in _WEEKLY_HOURS.finditer(text):
        number = _number(match["count"])
        if number is not None and 0 <= number <= 80:
            out.append((match.start(), number))
    return out


def has(text: str, pattern: re.Pattern[str]) -> bool:
    """Whether `pattern` occurs anywhere in `text`."""
    return pattern.search(text) is not None


def tokens(text: str) -> set[str]:
    """Lower-case word tokens; simple plurals folded ("pipelines" -> "pipeline")."""
    out = set()
    for word in _TOKEN.findall(text.lower()):
        folded = (
            word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
        )
        out.add(folded)
    return out


def distinctive(name: str) -> set[str]:
    """Name tokens that identify a work package (generic words like "setup" removed)."""
    return tokens(name) - _STOP


def code_in(code: str, text: str) -> bool:
    """A WBS code with at least two parts ("1.2", "1.1.3") mentioned as such."""
    if code.count(".") < 1:
        return False
    return re.search(rf"(?<![\d.]){re.escape(code)}(?![\d]|\.\d)", text) is not None


def format_days(days: float | int | None) -> str:
    """Working days as "4w" (whole weeks) or "7d"."""
    if days is None:
        return "—"
    whole_days = int(days)
    if whole_days % DAYS_PER_WEEK == 0:
        return f"{whole_days // DAYS_PER_WEEK}w"
    return f"{whole_days}d"


def format_money(cents: int | float | None) -> str:
    """Cents as "$365,000"."""
    if cents is None:
        return "—"
    return f"${int(cents) / 100:,.0f}"


def format_date(value: date | None) -> str:
    """A date as "Sep 30, 2025"."""
    return "—" if value is None else f"{value:%b} {value.day}, {value.year}"


def format_hours(value: float | int | None) -> str:
    """Hours as "20h"."""
    if value is None:
        return "—"
    return f"{float(value):g}h"


def unique(items: Iterable[str]) -> list[str]:
    """Items in first-seen order without repeats."""
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def first_name(full_name: str) -> str:
    """First word of a full_name name."""
    parts: Sequence[str] = full_name.split()
    return parts[0] if parts else full_name
