"""Tests of combining pasted notes and uploads within the intake size budget.

Purpose: Claude reads at most a fixed amount of text; the longest documents are cut first and the
prompt says so, so nothing is silently lost.
How it fits: Pure tests of `fit_sources` and `notes_block` in app/ai/intake.py; layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

from app.ai.intake import Source, fit_sources, notes_block


def _source(title: str, length: int) -> Source:
    """Return an upload of `length` characters."""
    return Source(uuid.uuid4(), title, "x" * length)


def test_sources_within_budget_are_kept_whole() -> None:
    """Sources within the budget are kept whole; blank ones drop."""
    sources = [_source("a", 10), _source("b", 20), Source(None, "blank", " \n ")]
    fitted = fit_sources(sources, budget=30)
    assert [(source.title, len(source.text), source.truncated) for source in fitted] == [
        ("a", 10, False),
        ("b", 20, False),
    ]


def test_longest_documents_are_cut_first_and_say_so() -> None:
    """The longest documents are cut first and marked as truncated in the prompt."""
    fitted = fit_sources([_source("short", 10), _source("long", 100), _source("mid", 40)], 60)
    assert [(source.title, len(source.text), source.truncated) for source in fitted] == [
        ("short", 10, False),
        ("long", 25, True),
        ("mid", 25, True),
    ]
    assert sum(len(source.text) for source in fitted) <= 60
    block = notes_block(fitted)
    assert "[Truncated: this document was too long to analyze in full; only its first 25" in block
    assert block.count("[Truncated") == 2
