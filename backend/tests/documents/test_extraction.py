"""Moved test support."""

from __future__ import annotations

import io

from app.ai.documents import (
    AUDIO_ERROR,
    MAX_CHARS,
    NO_TEXT_ERROR,
    UNREADABLE_ERROR,
    Extraction,
    clean_transcript,
    detect_kind,
    extract_text,
    file_title,
    is_video,
)
from app.domain.common import DocumentKind, JobStatus
from tests.support.documents import (
    docx_bytes,
    pdf_with_text,
)


# --------------------------------------------------------------------------- extraction
def test_detect_kind_by_extension_then_mime_type() -> None:
    assert detect_kind("Brief.PDF", "application/octet-stream") == DocumentKind.PDF
    assert detect_kind("notes.docx", None) == DocumentKind.DOCX
    for name in ("call.vtt", "call.srt", "notes.txt", "notes.md"):
        assert detect_kind(name, None) == DocumentKind.TRANSCRIPT
    assert detect_kind("call.m4a", None) == DocumentKind.AUDIO
    assert detect_kind("blob", "audio/mpeg") == DocumentKind.AUDIO
    assert detect_kind("blob", "application/pdf; charset=binary") == DocumentKind.PDF
    assert detect_kind("sheet.xlsx", "application/vnd.ms-excel") is None
    assert detect_kind(None, None) is None


def test_video_is_recognised_by_extension_or_mime_type() -> None:
    for name in ("call.mp4", "call.MOV", "a.m4v", "a.avi", "a.mkv", "a.wmv"):
        assert is_video(name, None)
    assert is_video("recording.webm", "video/webm")
    assert is_video("recording.webm", None)
    assert not is_video("recording.webm", "audio/webm")
    assert is_video("blob", "video/quicktime; codecs=avc1")
    assert not is_video("call.mp3", "audio/mpeg")
    assert not is_video("call.vtt", "text/vtt")


def test_file_title_strips_paths() -> None:
    assert file_title("C:\\Users\\jane\\brief.pdf") == "brief.pdf"
    assert file_title("  ") == "Untitled document"
    assert len(file_title("x" * 400 + ".pdf")) == 300


def test_pdf_and_docx_text() -> None:
    pdf = extract_text(DocumentKind.PDF, pdf_with_text("Budget approved at 375K"))
    assert pdf == Extraction(JobStatus.SUCCEEDED, "Budget approved at 375K")
    word = extract_text(DocumentKind.DOCX, docx_bytes())
    assert word.status == JobStatus.SUCCEEDED
    assert word.text == "Kickoff notes\nBudget | $375K"


def test_scanned_corrupt_and_audio_files_fail_with_fixed_messages() -> None:
    assert extract_text(DocumentKind.PDF, pdf_with_text("")) == Extraction.failed(NO_TEXT_ERROR)
    corrupt = extract_text(DocumentKind.PDF, b"%PDF-1.4 secret internals")
    assert corrupt == Extraction.failed(UNREADABLE_ERROR)
    assert extract_text(DocumentKind.DOCX, b"not a zip") == Extraction.failed(UNREADABLE_ERROR)
    assert extract_text(DocumentKind.AUDIO, b"ID3") == Extraction.failed(AUDIO_ERROR)
    assert extract_text(DocumentKind.TRANSCRIPT, b" \n\n ") == Extraction.failed(NO_TEXT_ERROR)


def test_webvtt_and_srt_keep_only_the_words() -> None:
    vtt = (
        "WEBVTT\nKind: captions\n\nNOTE recorded by Zoom\nsecond note line\n\n"
        "intro\n00:00:01.000 --> 00:00:04.000 align:start\n<v Jane Doe>Budget is <b>$375K</b>.\n\n"
        "00:00:05.000 --> 00:00:07.500\nGo-live by Q3.\n"
    )
    assert " ".join(clean_transcript(vtt).split()) == "Jane Doe: Budget is $375K. Go-live by Q3."
    srt = "1\n00:00:01,000 --> 00:00:02,000\nHello team\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n"
    assert clean_transcript(srt).split() == ["Hello", "team", "Bye"]


def test_plain_text_is_kept_verbatim_and_capped() -> None:
    notes = "OBJECTIVES:\n- Ship <fast>\r\n\n\n\n- Cut cost\x00"
    result = extract_text(DocumentKind.TRANSCRIPT, notes.encode() + b"\xff")
    assert result.text == "OBJECTIVES:\n- Ship <fast>\n\n- Cut cost\ufffd"
    assert not result.truncated
    huge = extract_text(DocumentKind.TRANSCRIPT, b"a" * (MAX_CHARS + 10))
    assert (len(huge.text or ""), huge.truncated) == (MAX_CHARS, True)
    from_stream = extract_text(DocumentKind.PDF, io.BytesIO(pdf_with_text("Scope")))
    assert from_stream == Extraction(JobStatus.SUCCEEDED, "Scope")
