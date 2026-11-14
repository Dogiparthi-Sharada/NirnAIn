"""Rule-based intake: the clearly labelled fallback when no Claude API key is set.

Runs record `model = 'rules'`. The extractor only copies what the notes say,
so every item is 'stated' and its text is its own quote:

- a heading picks the category of the bullets below it (case-insensitive,
  singular or plural; a heading is a short, non-bullet line that ends with
  ':', starts with '#', is bold, or is written in capitals or Title Case):
  OBJECTIVES / GOALS -> objective, REQUIREMENTS -> requirement,
  DELIVERABLES -> deliverable, CONSTRAINTS / BUDGET / TIMELINE -> constraint,
  RISKS / CONCERNS -> risk, OPEN ITEMS / UNKNOWNS / QUESTIONS -> missing_info;
  any other heading ends the current category;
- every bullet line (•, -, *, or numbered "1." / "1)") becomes one item;
- a bullet that names a budget / money amount or a deadline also becomes a
  constraint item, whatever heading it is under (bullets under unknown
  headings yield only that constraint item).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.ai.evidence import TEXT_LIMIT, ScopeSuggestion
from app.ai.intake import Source
from app.domain.common import Evidence, ScopeCategory

RULES_MODEL = "rules"
HEADING_MAX_WORDS = 6
_LOWERCASE_TITLE_WORDS = frozenset({"and", "or", "of", "the", "for", "to", "&", "/"})

_HEADING_KEYWORDS: tuple[tuple[re.Pattern[str], ScopeCategory], ...] = (
    (re.compile(r"\b(?:objectives?|goals?)\b"), ScopeCategory.OBJECTIVE),
    (re.compile(r"\brequirements?\b"), ScopeCategory.REQUIREMENT),
    (re.compile(r"\bdeliverables?\b"), ScopeCategory.DELIVERABLE),
    (re.compile(r"\b(?:constraints?|budgets?|timelines?)\b"), ScopeCategory.CONSTRAINT),
    (re.compile(r"\b(?:risks?|concerns?)\b"), ScopeCategory.RISK),
    (re.compile(r"\b(?:open items?|unknowns?|questions?)\b"), ScopeCategory.MISSING_INFO),
)
BULLET_LINE = re.compile(r"^\s*(?:[•\-*]|\d{1,3}[.)])\s+(?P<body>\S.*?)\s*$")
_MONTH = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
MONEY_MENTION = re.compile(
    r"[$€£]\s?\d|\b\d[\d,.]*\s?(?:k|m|bn|million|thousand|billion)?\s?"
    r"(?:usd|eur|gbp|dollars?|euros?|pounds?)\b|\bbudget",
    re.IGNORECASE,
)
DEADLINE_MENTION = re.compile(
    r"\b(?:deadlines?|due (?:by|on|date)|no later than|"
    rf"(?:by|before|until) (?:the )?(?:end of |mid[- ])?(?:{_MONTH}|q[1-4]|h[12]|"
    r"\d{1,2}[/.-]\d{1,2}|\d{4}|year[- ]end|(?:this|next) (?:week|month|quarter|year)))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _Heading:
    category: ScopeCategory | None  # None: a heading we don't map (ends the current section)


def _is_title_case(title: str) -> bool:
    """'Risks / Concerns', 'Open Items and Questions' (minor words may stay lower case)."""
    return all(
        word[0].isupper() or word.lower() in _LOWERCASE_TITLE_WORDS or not word[0].isalpha()
        for word in title.split()
    )


def _as_heading(line: str) -> _Heading | None:
    """The heading on this line, or None when the line is not a heading."""
    stripped = line.strip()
    if not stripped or BULLET_LINE.match(line):
        return None
    title = stripped.lstrip("#").strip().strip("*_").strip().rstrip(":").strip().strip("*_")
    if (
        not title
        or len(title.split()) > HEADING_MAX_WORDS
        or not any(character.isalpha() for character in title)
    ):
        return None
    looks_like_heading = (
        stripped.endswith(":")
        or stripped.startswith("#")
        or stripped.startswith(("**", "__"))
        or title == title.upper()
        or _is_title_case(title)
    )
    if not looks_like_heading:
        return None
    lowered = title.lower()
    for pattern, category in _HEADING_KEYWORDS:
        if pattern.search(lowered):
            return _Heading(category)
    return _Heading(None)


def _mentions_budget_or_deadline(text: str) -> bool:
    return bool(MONEY_MENTION.search(text) or DEADLINE_MENTION.search(text))


def _stated(category: ScopeCategory, bullet: str, source: Source) -> ScopeSuggestion:
    return ScopeSuggestion(
        category=category,
        text=bullet[:TEXT_LIMIT],
        confidence=None,
        source_quote=bullet,
        evidence=Evidence.STATED,
        evidence_note=None,
        suggested_text=None,
        source_document_id=source.document_id,
    )


def _source_suggestions(source: Source) -> list[ScopeSuggestion]:
    suggestions: list[ScopeSuggestion] = []
    current_category: ScopeCategory | None = None
    for line in source.text.splitlines():
        heading = _as_heading(line)
        if heading is not None:
            current_category = heading.category
            continue
        bullet_match = BULLET_LINE.match(line)
        if bullet_match is None:
            continue
        bullet = bullet_match.group("body")
        if current_category is not None:
            suggestions.append(_stated(current_category, bullet, source))
        if current_category != ScopeCategory.CONSTRAINT and _mentions_budget_or_deadline(bullet):
            suggestions.append(_stated(ScopeCategory.CONSTRAINT, bullet, source))
    return suggestions


def extract_by_rules(sources: Sequence[Source]) -> list[ScopeSuggestion]:
    """Scope items from headings and bullets; the same bullet twice in a category counts once."""
    seen: set[tuple[ScopeCategory, str]] = set()
    unique: list[ScopeSuggestion] = []
    for source in sources:
        for suggestion in _source_suggestions(source):
            key = (suggestion.category, " ".join(suggestion.text.lower().split()))
            if key not in seen:
                seen.add(key)
                unique.append(suggestion)
    return unique
