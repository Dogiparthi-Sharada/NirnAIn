"""Plain text from an uploaded file: PDF, Word (.docx) or a transcript.

Pure functions (bytes in, text out) so they are easy to test and can run in a
worker thread. Audio and anything else raise `UnsupportedFileError` (415).
"""

from __future__ import annotations

import io
import re
from pathlib import PurePath

from docx import Document
from pypdf import PdfReader

from app.core.errors import AppError

MAX_FILE_BYTES = 50 * 1024 * 1024

TEXT_SUFFIXES = {".txt", ".md"}
CAPTION_SUFFIXES = {".vtt", ".srt"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".webm", ".aac", ".flac", ".mp4"}
CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/vtt": ".vtt",
    "application/x-subrip": ".srt",
}

AUDIO_DETAIL = "Audio transcription is not available yet."
UNSUPPORTED_DETAIL = (
    "Unsupported file type. Use a PDF, a Word file (.docx) or a transcript (.txt, .vtt, .srt, .md)."
)
TOO_LARGE_DETAIL = "Files can be at most 50 MB."
EMPTY_DETAIL = "We couldn't find any text in that file."
UNREADABLE_DETAIL = "We couldn't read that file. Check that it isn't damaged or password-protected."

_TIMESTAMP = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->")
_CUE_NUMBER = re.compile(r"^\s*\d+\s*$")
_VOICE = re.compile(r"<v(?:\.[\w.-]+)?\s+([^>]+)>")
_TAG = re.compile(r"</?[^>]+>")


class UnsupportedFileError(AppError):
    status, slug, title = 415, "unsupported-media-type", "Unsupported Media Type"


class FileTooLargeError(AppError):
    status, slug, title = 413, "payload-too-large", "Payload Too Large"


class UnreadableFileError(AppError):
    status, slug, title = 422, "unprocessable", "Unprocessable Content"


def file_suffix(filename: str, content_type: str | None) -> str:
    """The file's type as a lower-case suffix, from its name or else its content type."""
    suffix = PurePath(filename).suffix.lower()
    if suffix:
        return suffix
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime.startswith("audio/"):
        return ".mp3"
    return CONTENT_TYPES.get(mime, "")


def supported_suffix(filename: str, content_type: str | None) -> str:
    """The file's suffix when we can read it; 415 for audio and unknown types."""
    suffix = file_suffix(filename, content_type)
    if suffix in AUDIO_SUFFIXES:
        raise UnsupportedFileError(AUDIO_DETAIL)
    if suffix not in TEXT_SUFFIXES | CAPTION_SUFFIXES | {".pdf", ".docx"}:
        raise UnsupportedFileError(UNSUPPORTED_DETAIL)
    return suffix


def extract_text(filename: str, content_type: str | None, data: bytes) -> str:
    suffix = supported_suffix(filename, content_type)
    if len(data) > MAX_FILE_BYTES:
        raise FileTooLargeError(TOO_LARGE_DETAIL)
    if suffix == ".pdf":
        text = pdf_text(data)
    elif suffix == ".docx":
        text = docx_text(data)
    elif suffix in CAPTION_SUFFIXES:
        text = caption_text(decode(data))
    else:
        text = decode(data)
    text = normalize(text)
    if not text:
        raise UnreadableFileError(EMPTY_DETAIL)
    return text


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def normalize(text: str) -> str:
    """Unix newlines, no trailing spaces, at most one blank line in a row."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted and not reader.decrypt("")
        pages = [] if encrypted else [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # any parser failure on an untrusted file
        raise UnreadableFileError(UNREADABLE_DETAIL) from exc
    if encrypted:
        raise UnreadableFileError(UNREADABLE_DETAIL)
    return "\n\n".join(pages)


def docx_text(data: bytes) -> str:
    try:
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                parts.append(" | ".join(dict.fromkeys(c for c in cells if c)))
    except Exception as exc:  # not a zip / not a Word document / malformed XML
        raise UnreadableFileError(UNREADABLE_DETAIL) from exc
    return "\n".join(parts)


def caption_text(raw: str) -> str:
    """WebVTT / SRT captions as plain lines; `<v Name>` voice tags become `Name: `."""
    out: list[str] = []
    block_start, skipping_block = True, False
    for line in raw.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            block_start, skipping_block = True, False
            continue
        first, block_start = block_start, False
        if skipping_block:
            continue
        if first and stripped.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            skipping_block = True
            continue
        if _TIMESTAMP.match(stripped) or _CUE_NUMBER.match(stripped):
            continue
        text = _TAG.sub("", _VOICE.sub(r"\1: ", stripped)).strip()
        if text and (not out or out[-1] != text):
            out.append(text)
    return "\n".join(out)
