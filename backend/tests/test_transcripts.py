"""Meeting transcripts without a Claude key: caption cleanup and the sentence-level rules."""

from __future__ import annotations

import uuid

from app.ai.evidence import QuoteFinder, ScopeSuggestion
from app.ai.intake import Source, with_spoken_text
from app.ai.intake_rules import MAX_ITEMS, extract_by_rules
from app.ai.intake_sentences import SENTENCE_MAX_CHARS
from app.ai.transcripts import is_transcript, split_speaker, spoken_text
from app.domain.common import Evidence, ScopeCategory

CALL_ID = uuid.uuid4()
# A Zoom cloud-recording transcript: cue numbers, speaker labels, sentences split over cues.
ZOOM_VTT = """WEBVTT

1
00:00:02.140 --> 00:00:05.310
Jane Doe: Okay, can everyone hear me?

2
00:00:05.900 --> 00:00:09.020
Robert Walsh: Yep, loud and clear.

3
00:00:09.500 --> 00:00:14.280
Jane Doe: Great. So the goal of this project is to replace

4
00:00:14.280 --> 00:00:18.650
Jane Doe: the old billing system with one platform for all regions.

5
00:00:19.100 --> 00:00:24.900
Robert Walsh: Right. Finance approved a budget of $375K, and we need to go live by Q3.

6
00:00:25.400 --> 00:00:31.020
Sara Kim: The new system must support single sign-on with Okta

7
00:00:31.020 --> 00:00:34.400
Sara Kim: and it should export invoices to SAP every night.

8
00:00:35.000 --> 00:00:40.200
<v Sara Kim>We'll migrate twelve years of invoice history in the first phase.</v>

9
00:00:41.000 --> 00:00:46.700
Robert Walsh: I'm worried the SAP vendor might delay the integration work.

10
00:00:47.300 --> 00:00:51.100
Jane Doe: Um, we only have 3 developers until the summer.

11
00:00:51.800 --> 00:00:56.000
Jane Doe: Who owns the data migration on the finance side?

12
00:00:56.500 --> 00:01:00.300
Robert Walsh: Honestly I'm not sure yet, we need to find out.

13
00:01:00.900 --> 00:01:03.000
Sara Kim: Sounds good, thanks everyone.
"""
ZOOM_ITEMS = [
    (
        "objective",
        "The goal of this project is to replace the old billing system with one platform "
        "for all regions.",
    ),
    ("constraint", "Finance approved a budget of $375K, and we need to go live by Q3."),
    (
        "requirement",
        "The new system must support single sign-on with Okta and it should export invoices "
        "to SAP every night.",
    ),
    ("deliverable", "We'll migrate twelve years of invoice history in the first phase."),
    ("risk", "I'm worried the SAP vendor might delay the integration work."),
    ("constraint", "We only have 3 developers until the summer."),
    ("missing_info", "Who owns the data migration on the finance side?"),
    ("missing_info", "Honestly I'm not sure yet, we need to find out."),
]
PROSE_NOTES = """Call with Acme Finance, 12 March

Acme wants to replace its invoicing tool before the end of the year. The main objective is to cut
month-end close from ten days to three. Everyone agreed the portal should support SSO
via Okta, e.g. for contractors too.

We will roll out the new portal to all 4 regions. Lunch was great.
Integration with the legacy ERP is a big concern. Who signs off on data retention?
- Training plan is TBD
- Nice people
"""


def _rows(items: list[ScopeSuggestion]) -> list[tuple[str, str]]:
    return [(item.category.value, item.text) for item in items]


def _assert_grounded(items: list[ScopeSuggestion], sources: list[Source]) -> None:
    """Every quote is verbatim in the analyzed (spoken) text, and grounding finds it."""
    finder = QuoteFinder(sources)
    for item in items:
        assert item.evidence == Evidence.STATED
        assert (item.evidence_note, item.suggested_text, item.confidence) == (None, None, None)
        assert item.source_quote is not None
        assert finder.locate(item.source_quote) == (item.source_quote, item.source_document_id)
        assert len(item.text) <= SENTENCE_MAX_CHARS


# --------------------------------------------------------------------------- captions
def test_speaker_labels_in_every_common_format() -> None:
    for line in (
        "Jane Doe: We must ship.",
        "[00:01:02] Jane Doe: We must ship.",
        "00:01:02 Jane Doe: We must ship.",
        "Jane Doe (00:01:02): We must ship.",
        "<v Jane Doe>We must ship.",
    ):
        spoken = split_speaker(line)
        assert (spoken.speaker, line[spoken.content_start :]) == ("Jane Doe", "We must ship.")
    assert split_speaker("Speaker 1: Hello there").speaker == "Speaker 1"
    assert split_speaker("Budget: $375K approved").speaker is None
    assert split_speaker("the goal is to ship").speaker is None


def test_zoom_captions_become_one_line_per_speaker_turn() -> None:
    assert spoken_text("kickoff.vtt", ZOOM_VTT).splitlines()[2:5] == [
        "Jane Doe: Great. So the goal of this project is to replace the old billing system "
        "with one platform for all regions.",
        "Robert Walsh: Right. Finance approved a budget of $375K, and we need to go live by Q3.",
        "Sara Kim: The new system must support single sign-on with Okta and it should export "
        "invoices to SAP every night. We'll migrate twelve years of invoice history in the "
        "first phase.",
    ]
    srt = (
        "1\n00:00:01,000 --> 00:00:03,000\nWe must ship\n\n"
        "2\n00:00:03,000 --> 00:00:05,000\nby Q3.\n"
    )
    assert spoken_text("call.srt", srt) == "We must ship by Q3."


def test_spoken_text_is_idempotent_and_leaves_notes_alone() -> None:
    once = spoken_text("kickoff.vtt", ZOOM_VTT)
    assert spoken_text("kickoff.vtt", once) == once
    assert spoken_text("notes.md", PROSE_NOTES) == PROSE_NOTES
    assert not is_transcript("notes.md", PROSE_NOTES)
    assert is_transcript("notes.txt", once)
    assert is_transcript("call.srt", "Hello")


# --------------------------------------------------------------------------- rules on meetings
def test_rules_read_a_zoom_transcript() -> None:
    upload = Source(CALL_ID, "kickoff.vtt", ZOOM_VTT)
    items = extract_by_rules([upload])
    assert _rows(items) == ZOOM_ITEMS
    assert {item.source_document_id for item in items} == {CALL_ID}
    assert items[0].source_quote == (
        "So the goal of this project is to replace the old billing system with one platform "
        "for all regions."
    )
    _assert_grounded(items, with_spoken_text([upload]))


def test_raw_captions_pasted_as_notes_and_old_caption_lines_read_the_same() -> None:
    pasted = Source(None, "Pasted notes", "\n" + ZOOM_VTT)
    assert _rows(extract_by_rules([pasted])) == ZOOM_ITEMS
    # Transcripts uploaded before captions were merged: one "Speaker: words" line per cue.
    old_lines = "\n".join(line for line in ZOOM_VTT.splitlines()[2:] if line and "-->" not in line)
    old_lines = "\n".join(line for line in old_lines.splitlines() if not line.isdigit())
    old_lines = old_lines.replace("<v Sara Kim>", "Sara Kim: ").replace("</v>", "")
    stored = Source(CALL_ID, "notes.txt", old_lines)
    items = extract_by_rules([stored])
    assert _rows(items) == ZOOM_ITEMS
    _assert_grounded(items, with_spoken_text([stored]))


def test_rules_read_plain_prose_notes() -> None:
    items = extract_by_rules([Source(None, "Pasted notes", PROSE_NOTES)])
    assert _rows(items) == [
        ("constraint", "Acme wants to replace its invoicing tool before the end of the year."),
        ("objective", "The main objective is to cut month-end close from ten days to three."),
        (
            "requirement",
            "Everyone agreed the portal should support SSO via Okta, e.g. for contractors too.",
        ),
        ("deliverable", "We will roll out the new portal to all 4 regions."),
        ("risk", "Integration with the legacy ERP is a big concern."),
        ("missing_info", "Who signs off on data retention?"),
        ("missing_info", "Training plan is TBD"),
    ]
    # A sentence wrapped over two lines is quoted verbatim, line break included.
    assert items[1].source_quote == (
        "The main objective is to cut\nmonth-end close from ten days to three."
    )
    _assert_grounded(items, [Source(None, "Pasted notes", PROSE_NOTES)])


def test_captions_without_punctuation_are_cut_into_short_quotes() -> None:
    words = " ".join(["we must support the old warehouse scanners"] * 30)
    upload = Source(CALL_ID, "call.vtt", f"Jane: {words}")
    items = extract_by_rules([upload])
    assert len(items) == 1  # every chunk says the same thing
    assert items[0].category == ScopeCategory.REQUIREMENT
    assert len(items[0].source_quote or "") <= SENTENCE_MAX_CHARS
    _assert_grounded(items, [upload])


def test_near_duplicates_count_once_and_the_cap_spreads_across_categories() -> None:
    repeated = (
        "Jane: We must support single sign-on for every regional office.\n"
        "Bob: Yes, we must support single sign-on for every regional office too.\n"
        "Jane: Agreed, we must support single sign-on for every regional office."
    )
    assert len(extract_by_rules([Source(None, "call.vtt", repeated)])) == 1

    requirements = [
        f"Jane: The portal must support report {index} and export {index + 1000} rows."
        for index in range(90)
    ]
    risks = [f"Bob: Supplier {index} is a risk for batch {index + 500}." for index in range(90)]
    objectives = ["Ann: Our goal is to close the books in three days."]
    upload = Source(CALL_ID, "call.vtt", "\n".join([*requirements, *risks, *objectives]))
    items = extract_by_rules([upload])
    counts = dict.fromkeys(ScopeCategory, 0)
    for item in items:
        counts[item.category] += 1
    assert len(items) == MAX_ITEMS
    assert counts[ScopeCategory.OBJECTIVE] == 1
    # Round-robin: the objective, then requirements and risks in turn (requirements first).
    assert (counts[ScopeCategory.REQUIREMENT], counts[ScopeCategory.RISK]) == (30, 29)
    assert items == sorted(items, key=lambda item: upload.text.index(item.source_quote or ""))
