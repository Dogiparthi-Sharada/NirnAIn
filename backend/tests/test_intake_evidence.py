"""Tests of the scope evidence: quote verification and the rule-based extractor.

Purpose: Every AI suggestion must quote the notes verbatim or be marked as inferred, and without an
API key the rules must still find bulleted items under known headings.
How it fits: Pure tests of app/ai/evidence.py, intake_llm.py and intake_rules.py; layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

from app.ai.evidence import NO_QUOTE_NOTE, QuoteFinder, ScopeSuggestion, grounded_suggestion
from app.ai.intake import Source
from app.ai.intake_llm import ExtractedItem, suggestions_from_model
from app.ai.intake_rules import extract_by_rules
from app.domain.common import Evidence, ScopeCategory

UPLOAD_ID = uuid.uuid4()
PASTED = Source(None, "Pasted notes", "Kickoff:\nBudget  approved at\n$375K by the board.")
UPLOAD = Source(UPLOAD_ID, "brief.pdf", "The SAP vendor\u2019s timeline is uncertain. Use <SSO>.")


# --------------------------------------------------------------------------- quotes
def test_quotes_match_ignoring_case_whitespace_and_typography() -> None:
    """Quotes are found despite case, spacing, curly quotes and escaping, and return verbatim."""
    finder = QuoteFinder([PASTED, UPLOAD])
    assert finder.locate("budget approved at $375k") == ("Budget  approved at\n$375K", None)
    assert finder.locate("  vendor's TIMELINE is uncertain ") == (
        "vendor\u2019s timeline is uncertain",
        UPLOAD_ID,
    )
    assert finder.locate("Use &lt;SSO&gt;.") == ("Use <SSO>.", UPLOAD_ID)
    assert finder.locate("Budget approved at $400K") is None
    assert finder.locate("   ") is None


def _ground(
    quote: str | None, evidence: Evidence, suggested: str | None = "Fix"
) -> ScopeSuggestion:
    """Return the suggestion grounded from a claimed quote and evidence in the two sources."""
    return grounded_suggestion(
        QuoteFinder([PASTED, UPLOAD]),
        category=ScopeCategory.CONSTRAINT,
        text="  Budget is $375K  ",
        confidence=0.7,
        claimed_quote=quote,
        claimed_evidence=evidence,
        note="Because",
        suggested_text=suggested,
    )


def test_unverifiable_quotes_are_dropped_and_marked_inferred() -> None:
    """A missing or unfound quote is dropped and the suggestion marked inferred."""
    for quote in (None, "Budget is $500K", ""):
        suggestion = _ground(quote, Evidence.STATED)
        assert (suggestion.source_quote, suggestion.evidence, suggestion.evidence_note) == (
            None,
            Evidence.INFERRED,
            NO_QUOTE_NOTE,
        )
        assert (suggestion.suggested_text, suggestion.source_document_id) == (None, None)


def test_verified_evidence_is_kept_with_the_verbatim_quote() -> None:
    """A found quote is kept verbatim with the claimed evidence, note and suggested text."""
    stated = _ground("budget approved at $375K", Evidence.STATED)
    assert stated.text == "Budget is $375K"
    assert (stated.source_quote, stated.evidence) == ("Budget  approved at\n$375K", Evidence.STATED)
    assert (stated.evidence_note, stated.suggested_text) == (None, None)

    inferred = _ground("timeline is uncertain", Evidence.INFERRED)
    assert (inferred.evidence, inferred.evidence_note, inferred.suggested_text) == (
        Evidence.INFERRED,
        "Because",
        None,
    )
    assert inferred.source_document_id == UPLOAD_ID

    flagged = _ground("approved at $375K", Evidence.FLAGGED, suggested="Budget approved: $375K")
    assert (flagged.evidence, flagged.evidence_note, flagged.suggested_text) == (
        Evidence.FLAGGED,
        "Because",
        "Budget approved: $375K",
    )


def test_model_items_with_unknown_categories_or_blank_text_are_dropped() -> None:
    """Unknown categories and blank texts are dropped; confidence is capped at 1."""

    def item(category: str, text: str) -> ExtractedItem:
        """Return a model item quoting "Kickoff"."""
        return ExtractedItem(
            category=category,
            text=text,
            confidence=0.5,
            source_quote="Kickoff",
            evidence=Evidence.STATED,
            evidence_note=None,
            suggested_text=None,
        )

    overconfident = item("risk", "Kickoff")
    overconfident.confidence = 1.7
    suggestions = suggestions_from_model(
        [overconfident, item("nope", "x"), item("goal", " ")], [PASTED]
    )
    assert [(suggestion.category, suggestion.text) for suggestion in suggestions] == [
        (ScopeCategory.RISK, "Kickoff")
    ]
    assert suggestions[0].confidence == 1.0


# --------------------------------------------------------------------------- rules fallback
NOTES = """Project kickoff (Jan 8)

OBJECTIVES:
• Consolidate 7 legacy BI systems
- Real-time dashboards for all units

## Requirements
1. SSO with Okta
2) Data refreshed hourly; go-live by Q3 2025

**Deliverables**
* Unified data platform

Budget & Timeline:
- Budget approved at $375K

Risks / Concerns
- SAP vendor timeline uncertain

Attendees:
- Jane Doe (PM)
- Finance wants the $20K licence renewed

OPEN QUESTIONS
- Who owns data quality?
Plain sentence that is not a bullet.
"""


def test_rules_extract_bullets_under_known_headings() -> None:
    """Bullets under known headings become stated items quoted from the upload."""
    suggestions = extract_by_rules([Source(UPLOAD_ID, "kickoff.txt", NOTES)])
    assert [(suggestion.category.value, suggestion.text) for suggestion in suggestions] == [
        ("objective", "Consolidate 7 legacy BI systems"),
        ("objective", "Real-time dashboards for all units"),
        ("requirement", "SSO with Okta"),
        ("requirement", "Data refreshed hourly; go-live by Q3 2025"),
        ("constraint", "Data refreshed hourly; go-live by Q3 2025"),
        ("deliverable", "Unified data platform"),
        ("constraint", "Budget approved at $375K"),
        ("risk", "SAP vendor timeline uncertain"),
        ("constraint", "Finance wants the $20K licence renewed"),
        ("missing_info", "Who owns data quality?"),
    ]
    for suggestion in suggestions:
        assert suggestion.source_quote == suggestion.text
        assert suggestion.source_quote in NOTES
        assert suggestion.evidence == Evidence.STATED
        assert suggestion.source_document_id == UPLOAD_ID
        assert (suggestion.evidence_note, suggestion.suggested_text) == (None, None)


def test_rules_skip_duplicates_and_notes_without_headings() -> None:
    """Repeated items count once; bullets without a known heading are ignored."""
    pasted = Source(None, "Pasted notes", "RISKS\n- Vendor delay\n")
    upload = Source(UPLOAD_ID, "notes.md", "# Risk\n- vendor  DELAY\n- Hiring freeze\n")
    suggestions = extract_by_rules([pasted, upload])
    assert [(suggestion.text, suggestion.source_document_id) for suggestion in suggestions] == [
        ("Vendor delay", None),
        ("Hiring freeze", UPLOAD_ID),
    ]
    assert extract_by_rules([Source(None, "Pasted notes", "- just a list\n- of things")]) == []
