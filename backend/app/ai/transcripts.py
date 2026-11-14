"""Meeting transcripts: caption cleanup and speaker labels.

WebVTT / SRT captions (Zoom, Teams, Meet exports) are cleaned when a file is
uploaded: the header, NOTE/STYLE/REGION blocks, cue numbers and timing lines
go, `<v Speaker>` voice tags become "Speaker: ", and consecutive cues of the
same speaker are merged into one line. Captions split a sentence across cues,
so merging keeps every spoken sentence on one line and quotes stay verbatim.

`split_speaker` recognises the labels in front of spoken lines ("Jane Doe:",
"[00:01:02] Jane:", "Speaker 1:", "<v Jane>") for the rule-based extractor.
`spoken_text` brings any notes source to the same shape when an intake run
starts: raw captions pasted as notes, and transcripts stored one caption per
line before uploads were merged, become one line per speaker turn, so
sentences (and the quotes taken from them) are no longer cut at cue
boundaries. Pure functions, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TIMESTAMP = r"(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}"
CUE_TIMING_LINE = re.compile(rf"^\s*{_TIMESTAMP}\s*-->\s*{_TIMESTAMP}.*$")
CUE_NUMBER_LINE = re.compile(r"^\s*\d{1,6}\s*$")
VOICE_TAG = re.compile(r"<v(?:\.[^\s>]*)?\s+([^>]+)>")
CAPTION_MARKUP_TAG = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
_SKIPPED_BLOCKS = ("NOTE", "STYLE", "REGION")
# Closing quotes / brackets after a sentence end ("…done.” or "…(Q3).").
_CLOSERS = "\"')\\]\u201d\u2019"
SENTENCE_END = re.compile(rf"[.!?\u2026][{_CLOSERS}]*$")
# A merged caption line is closed at the next sentence end once it is this long.
MERGED_LINE_TARGET_CHARS = 400
CAPTION_FILE_SUFFIXES = (".vtt", ".srt")
# Notes count as a transcript when most of their lines are "Speaker: words".
SPEAKER_LINE_SHARE = 0.6
MIN_SPEAKER_LINES = 3

_CLOCK = r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?"
_LEADING_CLOCK = re.compile(rf"\s*(?:\[{_CLOCK}\]|\({_CLOCK}\)|{_CLOCK}(?=\s))\s*")
_LEADING_VOICE_TAG = re.compile(r"\s*<v(?:\.[^\s>]*)?\s+(?P<speaker>[^>]+)>\s*")
_NAME_WORD = "[A-Z][\\w'\u2019.-]*"
_NAME = rf"{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}}"
_SPEAKER_LABEL = re.compile(
    rf"\s*(?P<speaker>(?:Speaker|SPEAKER)[ _]?\d{{1,3}}|{_NAME})"
    rf"\s*(?:\[{_CLOCK}\]|\({_CLOCK}\))?\s*:\s+(?=\S)"
)
# "Budget: $375K" or "Action item: …" are labelled facts, not someone speaking.
NON_SPEAKER_LABELS = frozenset(
    {
        "a",
        "action",
        "action item",
        "action items",
        "agenda",
        "attendees",
        "budget",
        "concern",
        "concerns",
        "constraint",
        "constraints",
        "date",
        "deadline",
        "decision",
        "decisions",
        "deliverable",
        "deliverables",
        "dependency",
        "dependencies",
        "goal",
        "goals",
        "important",
        "issue",
        "issues",
        "next steps",
        "note",
        "notes",
        "objective",
        "objectives",
        "open items",
        "owner",
        "q",
        "question",
        "questions",
        "requirement",
        "requirements",
        "risk",
        "risks",
        "scope",
        "status",
        "summary",
        "tbd",
        "time",
        "timeline",
        "unknowns",
        "update",
    }
)


@dataclass(frozen=True, slots=True)
class SpokenLine:
    """Who speaks on a line (None when unlabelled) and where their words start."""

    speaker: str | None
    content_start: int


def split_speaker(line: str) -> SpokenLine:
    """Find a leading timestamp and speaker label; the words start after them."""
    position = 0
    clock = _LEADING_CLOCK.match(line)
    if clock:
        position = clock.end()
    voice = _LEADING_VOICE_TAG.match(line, position)
    if voice:
        return SpokenLine(voice.group("speaker").strip(), voice.end())
    label = _SPEAKER_LABEL.match(line, position)
    if label and label.group("speaker").lower() not in NON_SPEAKER_LABELS:
        return SpokenLine(label.group("speaker"), label.end())
    return SpokenLine(None, position)


def _skip_webvtt_header(lines: list[str]) -> list[str]:
    """Drop the `WEBVTT` line and the header block that follows it (up to the first blank)."""
    header_start = 0
    while header_start < len(lines) and not lines[header_start].strip():
        header_start += 1  # pasted captions often start with a blank line
    if header_start == len(lines) or not lines[header_start].lstrip().startswith("WEBVTT"):
        return lines
    header_end = header_start
    while header_end < len(lines) and lines[header_end].strip():
        header_end += 1
    return lines[header_end:]


def _cue_blocks(lines: list[str]) -> list[list[str]]:
    """Lines grouped into blocks separated by blank lines."""
    blocks: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            blocks[-1].append(line)
        elif blocks[-1]:
            blocks.append([])
    return [block for block in blocks if block]


def _cue_text(block: list[str]) -> str:
    """The spoken text of one cue: lines after the timing line, voice tags as "Name: "."""
    timing_index = next(
        (index for index, line in enumerate(block) if CUE_TIMING_LINE.match(line)), None
    )
    text_lines = block if timing_index is None else block[timing_index + 1 :]
    if timing_index is None and all(CUE_NUMBER_LINE.match(line) for line in text_lines):
        return ""
    joined = " ".join(line.strip() for line in text_lines)
    with_speakers = VOICE_TAG.sub(lambda match: f"{match.group(1).strip()}: ", joined)
    return " ".join(CAPTION_MARKUP_TAG.sub("", with_speakers).split())


def merge_cues(cue_texts: list[str]) -> str:
    """One line per speaker turn: consecutive cues of the same speaker are joined.

    An unlabelled cue continues the current speaker. Rolling captions that
    repeat the previous cue word for word are dropped.
    """
    merged_lines: list[tuple[str | None, str]] = []
    previous_cue = ""
    for cue in cue_texts:
        if not cue or cue == previous_cue:
            continue
        previous_cue = cue
        spoken = split_speaker(cue)
        words = cue[spoken.content_start :].strip()
        if not words:
            continue
        if merged_lines:
            speaker, line = merged_lines[-1]
            same_speaker = spoken.speaker is None or spoken.speaker == speaker
            line_is_long = len(line) >= MERGED_LINE_TARGET_CHARS and SENTENCE_END.search(line)
            if same_speaker and not line_is_long:
                merged_lines[-1] = (speaker, f"{line} {words}")
                continue
            if spoken.speaker is None:
                spoken = SpokenLine(speaker, 0)
        merged_lines.append((spoken.speaker, words))
    return "\n".join(f"{speaker}: {line}" if speaker else line for speaker, line in merged_lines)


def clean_transcript(text: str) -> str:
    """Keep the spoken words of a WebVTT / SRT file, one line per speaker turn.

    Text without cue timing lines (plain notes, .txt, .md) is returned
    unchanged, so quotes from it stay verbatim.
    """
    lines = _skip_webvtt_header(text.lstrip("\ufeff").splitlines())
    if not has_cue_timings(lines):
        return "\n".join(lines)
    cue_texts = [
        _cue_text(block)
        for block in _cue_blocks(lines)
        if not block[0].strip().startswith(_SKIPPED_BLOCKS)
    ]
    return merge_cues(cue_texts)


def has_cue_timings(lines: list[str]) -> bool:
    """Whether any line is a WebVTT / SRT cue timing line ("00:00:01.000 --> …")."""
    return any(CUE_TIMING_LINE.match(line) for line in lines)


def _mostly_speaker_lines(lines: list[str]) -> bool:
    """Most non-blank lines start with a speaker label (a chat or caption export)."""
    spoken_lines = [line for line in lines if line.strip()]
    labelled = sum(split_speaker(line).speaker is not None for line in spoken_lines)
    return labelled >= MIN_SPEAKER_LINES and labelled >= SPEAKER_LINE_SHARE * len(spoken_lines)


def is_transcript(title: str, text: str) -> bool:
    """A meeting transcript: a caption file, cue timings, or mostly "Speaker: words" lines."""
    lines = text.splitlines()
    return (
        title.lower().endswith(CAPTION_FILE_SUFFIXES)
        or has_cue_timings(lines)
        or _mostly_speaker_lines(lines)
    )


def spoken_text(title: str, text: str) -> str:
    """The source's text with transcripts reduced to one line per speaker turn.

    Raw captions are cleaned (`clean_transcript`); caption files and speaker
    labelled lines get consecutive lines of the same speaker merged. Other
    notes are returned unchanged. Idempotent, so it is safe to apply twice.
    """
    lines = text.lstrip("\ufeff").splitlines()
    if has_cue_timings(lines):
        return clean_transcript(text)
    if title.lower().endswith(CAPTION_FILE_SUFFIXES) or _mostly_speaker_lines(lines):
        return merge_cues([line.strip() for line in lines])
    return text
