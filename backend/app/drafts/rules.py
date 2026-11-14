"""Deterministic project-draft reader (model "rules").

The clearly labelled fallback when no AI key is configured or the AI call fails.
It fills a field only when the text says it, keeping the sentence it came from
as the quote; everything else stays missing for the PM. Nothing is guessed:
"by Q3 2025" becomes an *inferred* end date the PM has to confirm.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from app.ai.prompting import one_line
from app.domain.drafts import DateField, MoneyField, ProjectDraftFields, TextField
from app.domain.projects import MAX_SAFE_CENTS

Role = Literal["start", "end"]

NAME_LIMIT = 200
DESCRIPTION_LIMIT = 1000
DEPARTMENT_LIMIT = 120
SPONSOR_LIMIT = 200
TITLE_MAX_WORDS = 12
TITLE_MAX_CHARS = 150
ROLE_AFTER_CHARS = 40

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

_ISO_DATE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_MDY = re.compile(
    rf"\b(?P<mon>{_MONTH})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>\d{{4}})\b", re.I
)
_DMY = re.compile(
    rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<mon>{_MONTH})\.?,?\s+(?P<y>\d{{4}})\b", re.I
)
_QUARTER = re.compile(r"\bQ(?P<q>[1-4])\s*(?:FY\s*)?(?P<y>\d{4})\b", re.I)
_MONTH_YEAR = re.compile(rf"\b(?P<mon>{_MONTH})\.?\s+(?P<y>\d{{4}})\b", re.I)

_DEADLINE_BEFORE = re.compile(
    r"\b(?:by|until|before|no\s+later\s+than|not\s+later\s+than)\s+(?:the\s+)?(?:end\s+of\s+)?$",
    re.I,
)
_ROLE_WORDS = re.compile(
    r"(?P<start>\bstart\w*|\bkick[\s-]?off\w*|\bbegin\w*|\bcommenc\w*)"
    r"|(?P<end>\bgo[\s-]?live\b|\blaunch\w*|\bdeadline\w*|\bdue\b|\bdeliver\w*|\bfinish\w*"
    r"|\bcomplet\w*|\bend(?:s|ing)?\b(?!-)|\btarget\w*|\bwrap\w*\s+up)",
    re.I,
)

_AMOUNT = r"\d{1,3}(?:,\d{3})+|\d+"
_SCALE = r"k|mm|mn|m|bn|thousand|million|billion"
_MONEY = re.compile(
    rf"(?:US\$|\$|\bUSD\s?)(?P<a>{_AMOUNT})(?:\.(?P<ad>\d+))?(?:\s?(?P<as>{_SCALE})\b)?"
    rf"|\b(?P<b>{_AMOUNT})(?:\.(?P<bd>\d+))?\s?(?:(?P<bs>{_SCALE})\b|(?:USD|dollars)\b)",
    re.I,
)
_SCALES = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "mm": 1_000_000,
    "mn": 1_000_000,
    "million": 1_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
}
_BUDGET_WORDS = re.compile(r"\bbudget\w*|\bfunding\b|\bfunded\b", re.I)

_BULLET = re.compile(r"^\s*(?:[-*\u2022\u00b7\u25aa\u25e6\u2023\u2013\u2014>]+|\d{1,2}[.)])\s+")
_SEPARATORS = " \t:|-\u2013\u2014,("
_HEADING = re.compile(
    r"^\s*(?:#+\s*)?(?:project\s+|key\s+|main\s+|business\s+)?"
    r"(?P<h>objectives?|goals?|purpose|aims?|vision|overview|summary|background|description)\b"
    r"\s*(?:[:\-\u2013\u2014]\s*(?P<rest>.*))?$",
    re.I,
)
_PRIMARY_HEADINGS = ("objective", "goal", "purpose", "aim", "vision")
_ALL_CAPS_HEADING = re.compile(r"^[A-Z][A-Z0-9 &/,'()-]{2,60}:?$")
_GOAL_SENTENCE = re.compile(
    r"\b(?:goal|objective|aim|purpose)\s+(?:of\s+[\w\s-]{1,60}?\s+)?(?:is|are|will\s+be)\s+to\b"
    r"|\bwe\s+(?:want|need|plan|aim|intend)\s+to\b"
    r"|\bthis\s+project\s+(?:will|aims\s+to|is\s+(?:meant|intended)\s+to)\b",
    re.I,
)

_LABELLED_NAME = re.compile(
    r"^\s*project(?:\s+name|\s+title)?\s*[:\u2013\u2014]\s*(?P<v>.+?)\s*$", re.I
)
_SPONSOR = re.compile(
    r"^\s*(?:executive\s+|project\s+)?sponsor\s*[:\-\u2013\u2014]\s*(?P<v>.+?)\s*$", re.I
)
_DEPARTMENT = re.compile(
    r"^\s*(?:department|dept\.?|business\s+unit|division)\s*[:\-\u2013\u2014]\s*(?P<v>.+?)\s*$",
    re.I,
)
_TITLE_PREFIX = re.compile(
    r"^(?:(?:meeting|kick[\s-]?off|project|planning|discovery|workshop|call)\s+)?"
    r"(?:notes|minutes|transcript|summary|recap|agenda)\b(?:\s+(?:from|for|of|on))?"
    r"[\s:|\-\u2013\u2014]*",
    re.I,
)
_TITLE_SUFFIX = re.compile(
    r"[\s:|\-\u2013\u2014(]*(?:project\s+)?(?:kick[\s-]?off|meeting|notes|minutes|transcript|sync"
    r"|workshop|call|session|review|planning|charter|brief|proposal|overview|requirements|recap"
    r"|summary)\)?\s*$",
    re.I,
)
_SPEAKER_LABEL = re.compile(r"^[^:]{1,40}:\s")


@dataclass(frozen=True, slots=True)
class _DateHit:
    start: int
    end: int
    first: date
    last: date
    exact: bool


# --------------------------------------------------------------------------- text helpers
def lines(text: str) -> Iterator[str]:
    """Non-empty lines without their bullet / list markers."""
    for raw in text.split("\n"):
        line = _BULLET.sub("", raw).strip()
        if line:
            yield line


def sentences(text: str) -> Iterator[str]:
    """Sentences (or clauses split at ';'), each a verbatim slice of `text`."""
    for line in lines(text):
        for part in re.split(r"(?<=[.!?;])\s+", line):
            clean = part.strip().rstrip(";,").strip()
            if clean:
                yield clean


# --------------------------------------------------------------------------- name
def _clean_title(line: str) -> str:
    name = _TITLE_PREFIX.sub("", line, count=1)
    for _ in range(4):
        before = name
        name = _strip_trailing_date(name)
        name = _TITLE_SUFFIX.sub("", name)
        if name == before:
            break
    return name.strip(_SEPARATORS + ")")


def _strip_trailing_date(text: str) -> str:
    for pattern in (_ISO_DATE, _MDY, _DMY, _QUARTER, _MONTH_YEAR):
        found = [m for m in pattern.finditer(text) if not text[m.end() :].strip(" )")]
        if found:
            return text[: found[-1].start()].rstrip(_SEPARATORS)
    return text


def _looks_like_title(line: str) -> bool:
    words = line.split()
    return (
        len(line) <= TITLE_MAX_CHARS
        and len(words) <= TITLE_MAX_WORDS
        and not line.endswith((".", "!", "?"))
        and _HEADING.match(line) is None
    )


def find_name(text: str) -> TextField:
    for line in lines(text):
        m = _LABELLED_NAME.match(line)
        if m and _looks_like_title(m["v"]):
            return TextField(value=m["v"][:NAME_LIMIT], quote=line, status="stated")
    first = next((raw.strip() for raw in text.split("\n") if raw.strip()), "")
    if not first or _BULLET.match(first) or not _looks_like_title(first):
        return TextField()
    if not _TITLE_PREFIX.match(first) and _SPEAKER_LABEL.match(first):
        return TextField()  # "Robert: let's start", "Date: Jan 15"
    name = _clean_title(first)
    if not re.search(r"[A-Za-z]", name):
        return TextField()
    return TextField(value=name[:NAME_LIMIT], quote=first, status="stated")


# --------------------------------------------------------------------------- description
def _first_sentence(line: str) -> str:
    return next(sentences(line), line)


def find_description(text: str) -> TextField:
    all_lines = list(lines(text))
    candidates: list[tuple[int, str]] = []
    for i, line in enumerate(all_lines):
        m = _HEADING.match(line)
        if m is None:
            continue
        rank = 0 if m["h"].lower().startswith(_PRIMARY_HEADINGS) else 1
        rest = (m["rest"] or "").strip()
        if rest:
            candidates.append((rank, _first_sentence(rest)))
        elif i + 1 < len(all_lines):
            nxt = all_lines[i + 1]
            if _HEADING.match(nxt) is None and _ALL_CAPS_HEADING.match(nxt) is None:
                candidates.append((rank, _first_sentence(nxt)))
    if candidates:
        best = min(candidates, key=lambda c: c[0])[1]
        return TextField(value=one_line(best, DESCRIPTION_LIMIT), quote=best, status="inferred")
    for sentence in sentences(text):
        if _GOAL_SENTENCE.search(sentence):
            return TextField(
                value=one_line(sentence, DESCRIPTION_LIMIT), quote=sentence, status="inferred"
            )
    return TextField()


# --------------------------------------------------------------------------- budget
def parse_money_cents(match: re.Match[str]) -> int | None:
    whole = match["a"] or match["b"]
    fraction = match["ad"] if match["a"] else match["bd"]
    scale = (match["as"] if match["a"] else match["bs"]) or ""
    amount = Decimal(f"{whole.replace(',', '')}.{fraction or '0'}")
    cents = int(amount * _SCALES.get(scale.lower(), 1) * 100)
    return cents if 0 < cents <= MAX_SAFE_CENTS else None


def find_budget(text: str) -> MoneyField:
    for sentence in sentences(text):
        if not _BUDGET_WORDS.search(sentence):
            continue
        for m in _MONEY.finditer(sentence):
            cents = parse_money_cents(m)
            if cents is not None:
                return MoneyField(value=cents, quote=sentence, status="stated")
    return MoneyField()


# --------------------------------------------------------------------------- dates
def _month(name: str) -> int:
    return _MONTHS.index(name[:3].lower()) + 1


def _date_hits(sentence: str) -> list[_DateHit]:
    hits: list[_DateHit] = []

    def free(m: re.Match[str]) -> bool:
        return all(m.end() <= h.start or m.start() >= h.end for h in hits)

    for pattern in (_ISO_DATE, _MDY, _DMY):
        for m in pattern.finditer(sentence):
            if not free(m):
                continue
            month = int(m["m"]) if "m" in pattern.groupindex else _month(m["mon"])
            try:
                day = date(int(m["y"]), month, int(m["d"]))
            except ValueError:
                continue
            hits.append(_DateHit(m.start(), m.end(), day, day, exact=True))
    for m in _QUARTER.finditer(sentence):
        if free(m):
            year, quarter = int(m["y"]), int(m["q"])
            first = date(year, 3 * quarter - 2, 1)
            last_month = 3 * quarter
            last = date(year, last_month, calendar.monthrange(year, last_month)[1])
            hits.append(_DateHit(m.start(), m.end(), first, last, exact=False))
    for m in _MONTH_YEAR.finditer(sentence):
        if free(m):
            year, month = int(m["y"]), _month(m["mon"])
            last = date(year, month, calendar.monthrange(year, month)[1])
            hits.append(_DateHit(m.start(), m.end(), date(year, month, 1), last, exact=False))
    return sorted(hits, key=lambda h: h.start)


def _role(before: str, after: str) -> Role | None:
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


def find_dates(text: str) -> tuple[DateField, DateField]:
    found: dict[tuple[Role, bool], DateField] = {}
    for sentence in sentences(text):
        hits = _date_hits(sentence)
        for i, hit in enumerate(hits):
            prev_end = hits[i - 1].end if i else 0
            next_start = hits[i + 1].start if i + 1 < len(hits) else len(sentence)
            role = _role(sentence[prev_end : hit.start], sentence[hit.end : next_start])
            if role is None or (role, hit.exact) in found:
                continue
            day = hit.first if role == "start" else hit.last
            field = DateField(
                value=day, quote=sentence, status="stated" if hit.exact else "inferred"
            )
            found[(role, hit.exact)] = field
    missing = DateField()
    start = found.get(("start", True)) or found.get(("start", False)) or missing
    end = found.get(("end", True)) or found.get(("end", False)) or missing
    return start, end


# --------------------------------------------------------------------------- labels
def _labelled(text: str, pattern: re.Pattern[str], limit: int) -> TextField:
    for line in lines(text):
        m = pattern.match(line)
        if m and len(m["v"]) <= limit:
            return TextField(value=m["v"], quote=line, status="stated")
    return TextField()


# --------------------------------------------------------------------------- entry point
def rules_draft(text: str) -> ProjectDraftFields:
    start, end = find_dates(text)
    return ProjectDraftFields(
        name=find_name(text),
        description=find_description(text),
        start_date=start,
        target_end_date=end,
        budget_cents=find_budget(text),
        department=_labelled(text, _DEPARTMENT, DEPARTMENT_LIMIT),
        sponsor=_labelled(text, _SPONSOR, SPONSOR_LIMIT),
    )
