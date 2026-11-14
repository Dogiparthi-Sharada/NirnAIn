"""Sentence-level reading for the rule-based intake extractor (no Claude key, or Claude failed).

Purpose: Real meetings are prose: cut text into sentences, keep each one's exact position, and turn
those matching a keyword rule into 'stated' items whose quote is the sentence as written, so quote
grounding finds it.
How it fits: Used by intake_rules.py with the rules in intake_keywords.py; pure; layer: ai.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.ai.evidence import ScopeSuggestion
from app.ai.intake_keywords import classify
from app.domain.common import Evidence

SENTENCE_MAX_CHARS = 300
# Transcripts are full of short chatter ("Not sure.", "Yeah, should be fine."), so a
# spoken sentence needs a few words to count; written notes are terse on purpose.
TRANSCRIPT_MIN_WORDS = 4
NOTES_MIN_WORDS = 2

# A sentence ends at . ! ? or … (plus closing quotes / brackets) followed by a space or the end.
_SENTENCE_END = re.compile(r"[.!?\u2026]+[\"')\]\u201d\u2019]*(?=\s|$)")
# Where an over-long sentence may be cut: after , ; : or around a dash.
_CLAUSE_BREAK = re.compile(r"[,;:](?=\s)|\s[-\u2013\u2014](?=\s)")
# A period after these words does not end a sentence ("e.g. Okta", "Dr. Smith").
ABBREVIATIONS = frozenset(
    {"e.g", "i.e", "etc", "vs", "approx", "incl", "dept", "mr", "mrs", "ms", "dr", "prof", "no"}
)
_FILLER_WORD = re.compile(r"\b(?:u+m+|u+h+|e+r+m+|h+m+)\b[,.]?\s*", re.IGNORECASE)
_LEADING_FILLER = re.compile(
    r"^(?:(?:so|and|but|okay|ok|yeah|yes|well|right|alright|now|like)\b[,.]?\s+)+",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Span:
    """A stretch of the source text: `text[start:end]`."""

    start: int
    end: int


def trimmed(text: str, span: Span) -> Span | None:
    """`span` without surrounding whitespace; None when nothing is left."""
    start, end = span.start, span.end
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return Span(start, end) if start < end else None


def _ends_sentence(text: str, match: re.Match[str], limit: int) -> bool:
    """Whether a matched `.`/`?`/`!` really ends a sentence (not "e.g." or "approx. ten")."""
    if not match.group().startswith("."):
        return True
    word_start = match.start()
    while word_start > 0 and not text[word_start - 1].isspace():
        word_start -= 1
    word = text[word_start : match.start()].lower().strip("(\"'")
    if word in ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
        return False  # an abbreviation or an initial ("J. Smith")
    next_position = match.end()
    while next_position < limit and text[next_position].isspace():
        next_position += 1
    return next_position >= limit or not text[next_position].islower()


def split_sentences(text: str, span: Span) -> list[Span]:
    """The sentences inside `span`, trimmed, in order."""
    pieces: list[Span] = []
    piece_start = span.start
    for match in _SENTENCE_END.finditer(text, span.start, span.end):
        if _ends_sentence(text, match, span.end):
            pieces.append(Span(piece_start, match.end()))
            piece_start = match.end()
    pieces.append(Span(piece_start, span.end))
    return [piece for piece in (trimmed(text, piece) for piece in pieces) if piece]


def _cut_position(text: str, start: int, window_end: int) -> int:
    """Where to end a chunk that must stop by `window_end`: a clause break, a space, or hard."""
    earliest = start + SENTENCE_MAX_CHARS // 3  # avoid tiny leading chunks
    clause_ends = [match.end() for match in _CLAUSE_BREAK.finditer(text, earliest, window_end)]
    if clause_ends:
        return clause_ends[-1]
    space = text.rfind(" ", earliest, window_end)
    return space if space > start else window_end


def limit_length(text: str, span: Span) -> list[Span]:
    """`span` cut into chunks of at most `SENTENCE_MAX_CHARS` (captions without punctuation)."""
    chunks: list[Span] = []
    start = span.start
    while span.end - start > SENTENCE_MAX_CHARS:
        cut = _cut_position(text, start, start + SENTENCE_MAX_CHARS)
        chunks.append(Span(start, cut))
        start = cut
    chunks.append(Span(start, span.end))
    return [chunk for chunk in (trimmed(text, chunk) for chunk in chunks) if chunk]


def cleaned(quote: str) -> str:
    """The item text: the sentence without fillers ("um", a leading "So,"), one space per gap."""
    words = " ".join(_FILLER_WORD.sub("", quote).split())
    words = _LEADING_FILLER.sub("", words) or words
    return words[:1].upper() + words[1:SENTENCE_MAX_CHARS]


def sentence_items(
    text: str, span: Span, document_id: uuid.UUID | None, min_words: int
) -> list[ScopeSuggestion]:
    """A 'stated' scope item for every sentence in `span` that a keyword rule matches.

    The quote is the sentence verbatim from `text`; sentences shorter than
    `min_words` words or matching no rule are skipped.
    """
    items: list[ScopeSuggestion] = []
    for sentence in split_sentences(text, span):
        for chunk in limit_length(text, sentence):
            quote = text[chunk.start : chunk.end]
            item_text = cleaned(quote)
            if len(item_text.split()) < min_words:
                continue
            category = classify(item_text)
            if category is None:
                continue
            items.append(
                ScopeSuggestion(
                    category=category,
                    text=item_text,
                    confidence=None,
                    source_quote=quote,
                    evidence=Evidence.STATED,
                    evidence_note=None,
                    suggested_text=None,
                    source_document_id=document_id,
                )
            )
    return items
