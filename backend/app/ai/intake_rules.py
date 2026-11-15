"""Rule-based intake: the clearly labelled fallback when Claude is not available.

Purpose: Without a key, or when a Claude call fails, still extract scope items, copying only
what the notes say: every item is 'stated' and quotes the notes verbatim (`model = 'rules'`).
- headings and bullets: a heading (short line ending with ':', '#', bold, capitals or Title
  Case) picks the category of the bullets below it (OBJECTIVES -> objective, REQUIREMENTS ->
  requirement, DELIVERABLES -> deliverable, CONSTRAINTS / BUDGET / TIMELINE -> constraint,
  RISKS -> risk, OPEN ITEMS / QUESTIONS -> missing_info); a bullet naming money or a deadline
  also becomes a constraint;
- sentences: prose and bullets outside a known heading are classified by keyword
  (intake_sentences.py, intake_keywords.py); transcripts only this way, one turn at a time.
Near-duplicates in a category count once, and a run keeps at most `MAX_ITEMS` items, shared
fairly between the categories.
How it fits: Called by the intake job (app/jobs/intake.py); layer: ai.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from app.ai.evidence import TEXT_LIMIT, ScopeSuggestion
from app.ai.intake import Source
from app.ai.intake_keywords import mentions_budget_or_deadline
from app.ai.intake_sentences import (
    NOTES_MIN_WORDS,
    TRANSCRIPT_MIN_WORDS,
    Span,
    sentence_items,
    trimmed,
)
from app.ai.transcripts import is_transcript, split_speaker, spoken_text
from app.domain.common import Evidence, ScopeCategory

RULES_MODEL = "rules"
MAX_ITEMS = 60
HEADING_MAX_WORDS = 6
# A line of at least this many characters that doesn't end a sentence continues on the
# next line (hard-wrapped PDF text); shorter lines in notes are separate items.
WRAPPED_LINE_MIN_CHARS = 40
# Items in one category whose words overlap this much say the same thing.
DUPLICATE_WORD_OVERLAP = 0.8
DUPLICATE_MIN_WORDS = 4
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
LINE = re.compile(r"^.*$", re.MULTILINE)
_SENTENCE_FINISHED = re.compile(r"[.!?:;\u2026][\"')\]\u201d\u2019]*$")
_WORD = re.compile(r"[a-z0-9$€£%]+")


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


def _stated(category: ScopeCategory, bullet: str, source: Source) -> ScopeSuggestion:
    """Return a 'stated' scope item that quotes the bullet verbatim."""
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


def _lines(text: str) -> Iterator[Span]:
    """Every line of `text` (without its line break) as a span."""
    for match in LINE.finditer(text):
        yield Span(match.start(), match.end())


def _continues(previous_line: str, line: str) -> bool:
    """Whether `line` carries on the sentence of a hard-wrapped `previous_line`."""
    previous = previous_line.strip()
    return (
        len(previous) >= WRAPPED_LINE_MIN_CHARS
        and not _SENTENCE_FINISHED.search(previous)
        and split_speaker(line).speaker is None
    )


def _note_items(source: Source, text: str) -> list[ScopeSuggestion]:
    """Notes: bullets under known headings, and every other sentence by keyword."""
    items: list[ScopeSuggestion] = []
    current_category: ScopeCategory | None = None
    paragraph: Span | None = None
    previous_line = ""

    def read_paragraph() -> None:
        """Add the sentence items of the paragraph read so far."""
        if paragraph is not None:
            items.extend(sentence_items(text, paragraph, source.document_id, NOTES_MIN_WORDS))

    for line_span in _lines(text):
        line = text[line_span.start : line_span.end]
        heading = _as_heading(line)
        bullet_match = BULLET_LINE.match(line)
        content = trimmed(text, line_span)
        if heading is not None or bullet_match is not None or content is None:
            read_paragraph()
            paragraph, previous_line = None, ""
        if heading is not None:
            current_category = heading.category
        elif bullet_match is not None:
            bullet = bullet_match.group("body")
            if current_category is None:
                body = Span(
                    line_span.start + bullet_match.start("body"),
                    line_span.start + bullet_match.end("body"),
                )
                items.extend(sentence_items(text, body, source.document_id, NOTES_MIN_WORDS))
                continue
            items.append(_stated(current_category, bullet, source))
            if current_category != ScopeCategory.CONSTRAINT and mentions_budget_or_deadline(bullet):
                items.append(_stated(ScopeCategory.CONSTRAINT, bullet, source))
        elif content is not None:
            if paragraph is not None and _continues(previous_line, line):
                paragraph = Span(paragraph.start, content.end)
            else:
                read_paragraph()
                paragraph = content
            previous_line = line
    read_paragraph()
    return items


def _transcript_items(source: Source, text: str) -> list[ScopeSuggestion]:
    """Transcripts: every speaker turn (one line, label dropped) read sentence by sentence."""
    items: list[ScopeSuggestion] = []
    for line_span in _lines(text):
        spoken = split_speaker(text[line_span.start : line_span.end])
        words = trimmed(text, Span(line_span.start + spoken.content_start, line_span.end))
        if words is not None:
            items.extend(sentence_items(text, words, source.document_id, TRANSCRIPT_MIN_WORDS))
    return items


def _source_items(source: Source) -> list[ScopeSuggestion]:
    """Candidate items of one source, in reading order."""
    text = spoken_text(source.title, source.text)
    if is_transcript(source.title, source.text):
        return _transcript_items(source, text)
    return _note_items(source, text)


def _words(text: str) -> tuple[str, ...]:
    """Return the lower-cased words of a text, for near-duplicate checks."""
    return tuple(_WORD.findall(text.lower()))


def _same_point(words: tuple[str, ...], other: tuple[str, ...]) -> bool:
    """Equal words, mostly the same words, or one longer item containing the other."""
    if words == other:
        return True
    word_set, other_set = set(words), set(other)
    if len(word_set & other_set) >= DUPLICATE_WORD_OVERLAP * len(word_set | other_set):
        return True
    shorter, longer = sorted((words, other), key=len)
    return len(shorter) >= DUPLICATE_MIN_WORDS and f" {' '.join(shorter)} " in (
        f" {' '.join(longer)} "
    )


def _without_near_duplicates(items: Sequence[ScopeSuggestion]) -> list[ScopeSuggestion]:
    """The first of every group of items that make the same point in the same category."""
    kept: list[ScopeSuggestion] = []
    kept_words: dict[ScopeCategory, list[tuple[str, ...]]] = {}
    for item in items:
        words = _words(item.text)
        seen = kept_words.setdefault(item.category, [])
        if any(_same_point(words, other) for other in seen):
            continue
        seen.append(words)
        kept.append(item)
    return kept


def _spread(items: Sequence[ScopeSuggestion], limit: int) -> list[ScopeSuggestion]:
    """At most `limit` items, taken round-robin per category, returned in reading order."""
    if len(items) <= limit:
        return list(items)
    positions_by_category: dict[ScopeCategory, deque[int]] = {}
    for position, item in enumerate(items):
        positions_by_category.setdefault(item.category, deque()).append(position)
    chosen: list[int] = []
    while len(chosen) < limit:
        for category in ScopeCategory:
            queue = positions_by_category.get(category)
            if queue and len(chosen) < limit:
                chosen.append(queue.popleft())
    return [items[position] for position in sorted(chosen)]


def extract_by_rules(sources: Sequence[Source]) -> list[ScopeSuggestion]:
    """Scope items from headings, bullets and keyword-matched sentences.

    Quotes are verbatim from each source's `spoken_text` (the job passes
    sources already in that form, so the quotes are in the analyzed notes).
    """
    candidates = [item for source in sources for item in _source_items(source)]
    return _spread(_without_near_duplicates(candidates), MAX_ITEMS)
