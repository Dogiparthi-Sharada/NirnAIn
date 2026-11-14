"""AI intake: the project notes the extractor reads.

A run analyzes its own pasted notes (if any) plus every uploaded document whose
text was read. Transcripts are first reduced to one line per speaker turn
(`with_spoken_text`), then each source becomes a `<document title="…">` inside
one `<project_notes>` block. Pure functions; `app.jobs.intake` does the I/O.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.ai.prompting import as_data
from app.ai.transcripts import spoken_text

# Upper bound for all notes sent in one request (Claude's context is far larger,
# but long inputs are slow and costly). Over it, the longest documents are cut first.
MAX_TOTAL_CHARS = 400_000
PASTED_TITLE = "Pasted notes"


@dataclass(frozen=True, slots=True)
class Source:
    """One piece of project notes: pasted text (no document id) or an uploaded document."""

    document_id: uuid.UUID | None
    title: str
    text: str
    truncated: bool = False


def _per_source_cap(lengths: Sequence[int], budget: int) -> int:
    """The largest per-source length cap that keeps the total within `budget`.

    Short sources keep all their text; the remaining budget is shared equally
    by the longer ones ("water filling"), so the longest are cut first.
    """
    remaining_budget, remaining_sources = budget, len(lengths)
    for length in sorted(lengths):
        fair_share = remaining_budget // remaining_sources
        if length > fair_share:
            return fair_share
        remaining_budget -= length
        remaining_sources -= 1
    return max(lengths, default=0)


def with_spoken_text(sources: Sequence[Source]) -> list[Source]:
    """Sources with caption transcripts merged into speaker turns (`spoken_text`).

    Both extractors read these texts and quotes are checked against them, so a
    sentence split over two captions can be quoted in one piece.
    """
    return [replace(source, text=spoken_text(source.title, source.text)) for source in sources]


def fit_sources(sources: Sequence[Source], budget: int = MAX_TOTAL_CHARS) -> list[Source]:
    """Drop blank sources; if the rest is too long, cut the longest documents first."""
    kept = [source for source in sources if source.text.strip()]
    lengths = [len(source.text) for source in kept]
    if sum(lengths) <= budget:
        return kept
    cap = _per_source_cap(lengths, budget)
    return [
        replace(source, text=source.text[:cap], truncated=True)
        if len(source.text) > cap
        else source
        for source in kept
    ]


def truncation_note(source: Source) -> str:
    """Tells the model (and anyone reading the prompt) that a document was cut."""
    return (
        f"[Truncated: this document was too long to analyze in full; only its first "
        f"{len(source.text):,} characters are included.]"
    )


def notes_block(sources: Sequence[Source]) -> str:
    """Every source as `<document title="…">` inside one `<project_notes>` block.

    Prompt-injection guard: angle brackets in titles and text are escaped, so a
    document can't close the block and pose as instructions.
    """
    parts: list[str] = []
    for source in sources:
        title = as_data(source.title).replace('"', "&quot;")
        body = as_data(source.text)
        if source.truncated:
            body += "\n" + truncation_note(source)
        parts.append(f'<document title="{title}">\n{body}\n</document>')
    return "<project_notes>\n" + "\n".join(parts) + "\n</project_notes>"
