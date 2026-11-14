"""Scope evidence ("never invent"): tie every extracted item to the notes' own words.

Whatever the extractor claims, an item keeps a `source_quote` only when that
quote really occurs in the notes. Matching ignores case, runs of whitespace
and typographic quote/dash variants, and the stored quote is the verbatim
span of the notes (not the extractor's rendering of it). An item whose quote
can't be found loses it and becomes 'inferred' with `NO_QUOTE_NOTE`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.ai.intake import Source
from app.domain.common import Evidence, ScopeCategory

NO_QUOTE_NOTE = "No exact quote found in the notes."
TEXT_LIMIT = 2000  # scope_items.text / suggested_text check
NOTE_LIMIT = 1000  # scope_items.evidence_note check

# One-to-one character replacements, so folded positions map back to the original.
_EQUIVALENT_CHARACTERS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
)


@dataclass(frozen=True, slots=True)
class ScopeSuggestion:
    """One extracted scope item, ready to be stored as a pending AI item."""

    category: ScopeCategory
    text: str
    confidence: float | None
    source_quote: str | None
    evidence: Evidence
    evidence_note: str | None
    suggested_text: str | None
    source_document_id: uuid.UUID | None  # set only when the quote is in an upload


def _folded(text: str) -> tuple[str, list[int]]:
    """`text` folded for matching, plus the original index of every folded character.

    Folding lower-cases, maps typographic quotes/dashes to ASCII and turns each
    run of whitespace into one space (leading whitespace is dropped).
    """
    characters: list[str] = []
    original_positions: list[int] = []
    previous_was_space = True
    for position, character in enumerate(text):
        if character.isspace():
            if not previous_was_space:
                characters.append(" ")
                original_positions.append(position)
            previous_was_space = True
            continue
        previous_was_space = False
        for folded_character in character.translate(_EQUIVALENT_CHARACTERS).lower():
            characters.append(folded_character)
            original_positions.append(position)
    return "".join(characters), original_positions


def _unescape_prompt_text(quote: str) -> str:
    """Undo the angle-bracket escaping applied to the notes in the prompt (`as_data`)."""
    return quote.replace("&lt;", "<").replace("&gt;", ">")


class QuoteFinder:
    """Finds quotes in the notes; each source is folded once, then searched many times."""

    def __init__(self, sources: Sequence[Source]) -> None:
        self._folded_sources = [(source, *_folded(source.text)) for source in sources]

    def locate(self, quote: str) -> tuple[str, uuid.UUID | None] | None:
        """The verbatim span matching `quote` and the id of the upload it is in.

        Sources are searched in order (pasted notes first); the document id is
        None for pasted notes. Returns None when the quote is nowhere in the notes.
        """
        folded_quote = _folded(_unescape_prompt_text(quote))[0].rstrip()
        if not folded_quote:
            return None
        for source, folded_text, original_positions in self._folded_sources:
            start = folded_text.find(folded_quote)
            if start < 0:
                continue
            first = original_positions[start]
            last = original_positions[start + len(folded_quote) - 1]
            return source.text[first : last + 1], source.document_id
        return None


def _clean(text: str | None, limit: int) -> str | None:
    """Trimmed text cut to `limit` characters; None when blank."""
    cleaned = (text or "").strip()[:limit]
    return cleaned or None


def grounded_suggestion(
    finder: QuoteFinder,
    *,
    category: ScopeCategory,
    text: str,
    confidence: float | None,
    claimed_quote: str | None,
    claimed_evidence: Evidence,
    note: str | None,
    suggested_text: str | None,
) -> ScopeSuggestion:
    """Keep the extractor's evidence only if its quote is really in the notes."""
    item_text = text.strip()[:TEXT_LIMIT]
    located = finder.locate(claimed_quote) if claimed_quote else None
    if located is None:
        # Never present an unverifiable quote: downgrade to inferred and say why.
        return ScopeSuggestion(
            category, item_text, confidence, None, Evidence.INFERRED, NO_QUOTE_NOTE, None, None
        )
    verbatim_quote, document_id = located
    # suggested_text belongs to flagged items only ("use the quoted version").
    suggestion = (
        _clean(suggested_text, TEXT_LIMIT) if claimed_evidence == Evidence.FLAGGED else None
    )
    evidence_note = None if claimed_evidence == Evidence.STATED else _clean(note, NOTE_LIMIT)
    return ScopeSuggestion(
        category,
        item_text,
        confidence,
        verbatim_quote,
        claimed_evidence,
        evidence_note,
        suggestion,
        document_id,
    )
