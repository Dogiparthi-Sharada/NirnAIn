"""Deterministic project-draft reader (model "rules").

Purpose: The clearly labelled fallback when no AI key is configured or the AI call fails. It fills a
field only when the text says it, keeping the sentence as the quote; nothing is guessed: "by Q3
2025" becomes an inferred end date the PM has to confirm.
How it fits: `app.drafts.reader.DraftReader` calls `rules_draft`; dates are parsed by
app/drafts/dates.py; pure; layer: drafts.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from decimal import Decimal

from app.ai.prompting import one_line
from app.domain.drafts import DateField, MoneyField, ProjectDraftFields, TextField
from app.domain.projects import MAX_SAFE_CENTS
from app.drafts.dates import Role, date_hits, date_role, strip_trailing_date

NAME_LIMIT = 200
DESCRIPTION_LIMIT = 1000
DEPARTMENT_LIMIT = 120
SPONSOR_LIMIT = 200
TITLE_MAX_WORDS = 12
TITLE_MAX_CHARS = 150

_AMOUNT = r"\d{1,3}(?:,\d{3})+|\d+"
_SCALE = r"k|mm|mn|m|bn|thousand|million|billion"
_MONEY = re.compile(
    rf"(?:US\$|\$|\bUSD\s?)(?P<amount>{_AMOUNT})(?:\.(?P<amount_decimals>\d+))?(?:\s?(?P<amount_scale>{_SCALE})\b)?"
    rf"|\b(?P<bare_amount>{_AMOUNT})(?:\.(?P<bare_decimals>\d+))?\s?(?:(?P<bare_scale>{_SCALE})\b|(?:USD|dollars)\b)",
    re.IGNORECASE,
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
_BUDGET_WORDS = re.compile(r"\bbudget\w*|\bfunding\b|\bfunded\b", re.IGNORECASE)

_BULLET = re.compile(r"^\s*(?:[-*\u2022\u00b7\u25aa\u25e6\u2023\u2013\u2014>]+|\d{1,2}[.)])\s+")
_SEPARATORS = " \t:|-\u2013\u2014,("
_HEADING = re.compile(
    r"^\s*(?:#+\s*)?(?:project\s+|key\s+|main\s+|business\s+)?"
    r"(?P<heading>objectives?|goals?|purpose|aims?|vision|overview|summary|background|description)\b"
    r"\s*(?:[:\-\u2013\u2014]\s*(?P<rest>.*))?$",
    re.IGNORECASE,
)
_PRIMARY_HEADINGS = ("objective", "goal", "purpose", "aim", "vision")
_ALL_CAPS_HEADING = re.compile(r"^[A-Z][A-Z0-9 &/,'()-]{2,60}:?$")
_GOAL_SENTENCE = re.compile(
    r"\b(?:goal|objective|aim|purpose)\s+(?:of\s+[\w\s-]{1,60}?\s+)?(?:is|are|will\s+be)\s+to\b"
    r"|\bwe\s+(?:want|need|plan|aim|intend)\s+to\b"
    r"|\bthis\s+project\s+(?:will|aims\s+to|is\s+(?:meant|intended)\s+to)\b",
    re.IGNORECASE,
)

_LABELLED_NAME = re.compile(
    r"^\s*project(?:\s+name|\s+title)?\s*[:\u2013\u2014]\s*(?P<value>.+?)\s*$", re.IGNORECASE
)
_SPONSOR = re.compile(
    r"^\s*(?:executive\s+|project\s+)?sponsor\s*[:\-\u2013\u2014]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_DEPARTMENT = re.compile(
    r"^\s*(?:department|dept\.?|business\s+unit|division)\s*[:\-\u2013\u2014]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_TITLE_PREFIX = re.compile(
    r"^(?:(?:meeting|kick[\s-]?off|project|planning|discovery|workshop|call)\s+)?"
    r"(?:notes|minutes|transcript|summary|recap|agenda)\b(?:\s+(?:from|for|of|on))?"
    r"[\s:|\-\u2013\u2014]*",
    re.IGNORECASE,
)
_TITLE_SUFFIX = re.compile(
    r"[\s:|\-\u2013\u2014(]*(?:project\s+)?(?:kick[\s-]?off|meeting|notes|minutes|transcript|sync"
    r"|workshop|call|session|review|planning|charter|brief|proposal|overview|requirements|recap"
    r"|summary)\)?\s*$",
    re.IGNORECASE,
)
_SPEAKER_LABEL = re.compile(r"^[^:]{1,40}:\s")


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
    """Drop "Meeting notes —" style prefixes and "Kickoff" / trailing-date suffixes."""
    name = _TITLE_PREFIX.sub("", line, count=1)
    for _ in range(4):
        before = name
        name = strip_trailing_date(name, _SEPARATORS)
        name = _TITLE_SUFFIX.sub("", name)
        if name == before:
            break
    return name.strip(_SEPARATORS + ")")


def _looks_like_title(line: str) -> bool:
    """Tell whether a line is short enough, unpunctuated and not a heading, so it can be a title."""
    words = line.split()
    return (
        len(line) <= TITLE_MAX_CHARS
        and len(words) <= TITLE_MAX_WORDS
        and not line.endswith((".", "!", "?"))
        and _HEADING.match(line) is None
    )


def find_name(text: str) -> TextField:
    """A "Project: X" line, else the title line ("Meeting Notes — X Kickoff" → "X"); stated."""
    for line in lines(text):
        labelled = _LABELLED_NAME.match(line)
        if labelled and _looks_like_title(labelled["value"]):
            return TextField(value=labelled["value"][:NAME_LIMIT], quote=line, status="stated")
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
    """Return the line's first sentence (the whole line when it has none)."""
    return next(sentences(line), line)


def find_description(text: str) -> TextField:
    """The first objective under an Objectives/Goals heading (else a "the goal is to …" sentence).

    Always *inferred*: a sentence picked as the description is a judgement the PM confirms.
    """
    all_lines = list(lines(text))
    candidates: list[tuple[int, str]] = []
    for index, line in enumerate(all_lines):
        heading = _HEADING.match(line)
        if heading is None:
            continue
        rank = 0 if heading["heading"].lower().startswith(_PRIMARY_HEADINGS) else 1
        rest = (heading["rest"] or "").strip()
        if rest:
            candidates.append((rank, _first_sentence(rest)))
        elif index + 1 < len(all_lines):
            next_line = all_lines[index + 1]
            if _HEADING.match(next_line) is None and _ALL_CAPS_HEADING.match(next_line) is None:
                candidates.append((rank, _first_sentence(next_line)))
    if candidates:
        best = min(candidates, key=lambda candidate: candidate[0])[1]
        return TextField(value=one_line(best, DESCRIPTION_LIMIT), quote=best, status="inferred")
    for sentence in sentences(text):
        if _GOAL_SENTENCE.search(sentence):
            return TextField(
                value=one_line(sentence, DESCRIPTION_LIMIT), quote=sentence, status="inferred"
            )
    return TextField()


# --------------------------------------------------------------------------- budget
def parse_money_cents(match: re.Match[str]) -> int | None:
    """Integer cents of a `_MONEY` match ("$375K" → 37_500_000); None when zero or too large."""
    with_currency = match["amount"] is not None
    whole = match["amount"] if with_currency else match["bare_amount"]
    fraction = match["amount_decimals"] if with_currency else match["bare_decimals"]
    scale = (match["amount_scale"] if with_currency else match["bare_scale"]) or ""
    amount = Decimal(f"{whole.replace(',', '')}.{fraction or '0'}")
    cents = int(amount * _SCALES.get(scale.lower(), 1) * 100)
    return cents if 0 < cents <= MAX_SAFE_CENTS else None


def find_budget(text: str) -> MoneyField:
    """The first amount in a sentence that talks about the budget; a lone cost is not a budget."""
    for sentence in sentences(text):
        if not _BUDGET_WORDS.search(sentence):
            continue
        for money in _MONEY.finditer(sentence):
            cents = parse_money_cents(money)
            if cents is not None:
                return MoneyField(value=cents, quote=sentence, status="stated")
    return MoneyField()


# --------------------------------------------------------------------------- dates
def find_dates(text: str) -> tuple[DateField, DateField]:
    """(start, target end) from dates next to start / deadline words.

    Exact dates are *stated*; quarters and months ("by Q3 2025") are *inferred* as their first or
    last day, and an exact date anywhere wins over them. Dates with no cue (e.g. the meeting's own
    date) are ignored rather than guessed.
    """
    found: dict[tuple[Role, bool], DateField] = {}
    for sentence in sentences(text):
        hits = date_hits(sentence)
        for index, hit in enumerate(hits):
            previous_end = hits[index - 1].end if index else 0
            next_start = hits[index + 1].start if index + 1 < len(hits) else len(sentence)
            role = date_role(sentence[previous_end : hit.start], sentence[hit.end : next_start])
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
    """The value of the first "Label: value" line matching `pattern` (e.g. "Sponsor: …")."""
    for line in lines(text):
        labelled = pattern.match(line)
        if labelled and len(labelled["value"]) <= limit:
            return TextField(value=labelled["value"], quote=line, status="stated")
    return TextField()


# --------------------------------------------------------------------------- entry point
def rules_draft(text: str) -> ProjectDraftFields:
    """Every project field the rules can support from `text`; the rest stay missing."""
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
