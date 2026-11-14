"""Text helpers for reading meeting notes: sentences, money, dates, durations, hours, names."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

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
_NUM = (
    r"\d+(?:\.\d+)?|a couple of|couple of|a few|an?|one|two|three|four|five|six|seven|eight"
    r"|nine|ten|eleven|twelve"
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_SPEAKER = re.compile(r"^(?P<speaker>[A-Z][\w .'&()/-]{0,48}?)\s*:\s+(?P<body>\S.*)$", re.S)
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
_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_MDY = re.compile(
    rf"\b(?P<mon>{_MONTH})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<y>\d{{4}}))?", re.I
)
_DMY = re.compile(
    rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{_MONTH})\b\.?(?:,?\s+(?P<y>\d{{4}}))?",
    re.I,
)
_SPAN = re.compile(
    rf"\b(?P<n>{_NUM})\s*(?:more\s+|extra\s+|additional\s+|further\s+)?"
    r"(?P<unit>weeks?|wks?|(?:working |business |work )?days?|months?)\b",
    re.I,
)
_HOURS = re.compile(
    rf"\b(?P<n>{_NUM})\s*(?:hours?|hrs?|h)\b(?:\s*(?:a|per|/|each)\s*(?:week|wk))?", re.I
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
    text: str  # the whole sentence, verbatim
    body: str  # without a leading "Name:" speaker prefix, verbatim
    speaker: str | None


def sentences(text: str) -> list[Sentence]:
    out: list[Sentence] = []
    for raw in _SPLIT.split(text):
        s = _BULLET.sub("", raw).strip()
        if not s:
            continue
        m = _SPEAKER.match(s)
        if m:
            out.append(Sentence(s, m["body"].strip(), m["speaker"].strip()))
        else:
            out.append(Sentence(s, s, None))
    return out


def _number(raw: str) -> float | None:
    key = raw.lower()
    if key in _WORDS:
        return float(_WORDS[key])
    try:
        return float(key)
    except ValueError:
        return None


def money_cents(text: str) -> list[int]:
    """Money amounts in a sentence as integer cents, in order of appearance."""
    found: list[tuple[int, int]] = []
    for rx in (_MONEY, _MONEY_WORDS):
        for m in rx.finditer(text):
            try:
                value = Decimal(m["num"].replace(",", ""))
            except InvalidOperation:
                continue
            unit = (m["unit"] or "").lower()
            value *= _UNITS.get(unit, 1)
            found.append((m.start(), int(value * 100)))
    found.sort()
    seen: list[int] = []
    for _, cents in found:
        if cents > 0 and cents not in seen:
            seen.append(cents)
    return seen


def dates(text: str, default_year: int) -> list[date]:
    """Calendar dates in a sentence. A date without a year takes `default_year`."""
    found: list[tuple[int, date]] = []
    for m in _ISO.finditer(text):
        try:
            found.append((m.start(), date(int(m["y"]), int(m["m"]), int(m["d"]))))
        except ValueError:
            continue
    for rx in (_MDY, _DMY):
        for m in rx.finditer(text):
            month = _MONTHS[m["mon"][:3].lower()]
            year = int(m["y"]) if m["y"] else default_year
            try:
                found.append((m.start(), date(year, month, int(m["d"]))))
            except ValueError:
                continue
    found.sort()
    out: list[date] = []
    for _, d in found:
        if d not in out:
            out.append(d)
    return out


def spans_days(text: str) -> list[int]:
    """Durations ("two weeks", "3 days", "1 month") as working days."""
    out: list[int] = []
    for m in _SPAN.finditer(text):
        n = _number(m["n"])
        if n is None or n <= 0:
            continue
        unit = m["unit"].lower()
        if unit.startswith("w"):
            days = n * DAYS_PER_WEEK
        elif unit.startswith("month"):
            days = n * DAYS_PER_MONTH
        else:
            days = n
        out.append(round(days))
    return out


def hours(text: str) -> list[float]:
    out: list[float] = []
    for m in _HOURS.finditer(text):
        n = _number(m["n"])
        if n is not None and 0 <= n <= 80:
            out.append(n)
    return out


def has(text: str, pattern: re.Pattern[str]) -> bool:
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
    return tokens(name) - _STOP


def code_in(code: str, text: str) -> bool:
    """A WBS code with at least two parts ("1.2", "1.1.3") mentioned as such."""
    if code.count(".") < 1:
        return False
    return re.search(rf"(?<![\d.]){re.escape(code)}(?![\d]|\.\d)", text) is not None


def fmt_days(days: float | int | None) -> str:
    if days is None:
        return "—"
    d = int(days)
    if d % DAYS_PER_WEEK == 0:
        return f"{d // DAYS_PER_WEEK}w"
    return f"{d}d"


def fmt_money(cents: int | float | None) -> str:
    if cents is None:
        return "—"
    return f"${int(cents) / 100:,.0f}"


def fmt_date(d: date | None) -> str:
    return "—" if d is None else f"{d:%b} {d.day}, {d.year}"


def fmt_hours(h: float | int | None) -> str:
    if h is None:
        return "—"
    return f"{float(h):g}h"


def unique(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for i in items:
        if i not in out:
            out.append(i)
    return out


def first_name(full: str) -> str:
    parts: Sequence[str] = full.split()
    return parts[0] if parts else full
