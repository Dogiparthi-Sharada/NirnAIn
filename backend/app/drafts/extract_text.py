"""Plain text from an uploaded file: PDF, Word (.docx) or a transcript.

Used by `POST /orgs/{orgId}/project-drafts/extract` (`app.api.v1.drafts`) before
the draft is read. Pure, blocking functions (bytes or a seekable stream in, text
out) so they are easy to test and can run in a worker thread. Files may be up
to 100 MB, like intake documents (`app.ai.documents`); a stream is read in
place, never copied into one big bytes object. Video, audio and anything else
raise `UnsupportedFileError` (415); files are never stored here.
"""

from __future__ import annotations

import codecs
import io
import re
from pathlib import PurePath
from typing import BinaryIO

from docx import Document
from pypdf import PdfReader

from app.ai.documents import (
    MAX_UPLOAD_BYTES,
    TEXT_READ_LIMIT_BYTES,
    TOO_LARGE_DETAIL,
    VIDEO_DETAIL,
    is_video,
    stream_size,
)
from app.core.errors import AppError, ContentTooLargeError

TEXT_SUFFIXES = {".txt", ".md"}
CAPTION_SUFFIXES = {".vtt", ".srt"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".webm", ".aac", ".flac"}
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
EMPTY_DETAIL = "We couldn't find any text in that file."
UNREADABLE_DETAIL = "We couldn't read that file. Check that it isn't damaged or password-protected."

_TIMESTAMP = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->")
_CUE_NUMBER = re.compile(r"^\s*\d+\s*$")
_VOICE = re.compile(r"<v(?:\.[\w.-]+)?\s+([^>]+)>")
_TAG = re.compile(r"</?[^>]+>")


class UnsupportedFileError(AppError):
    """415: video, audio (no speech-to-text yet) or a type we can't read."""

    status, slug, title = 415, "unsupported-media-type", "Unsupported Media Type"


class UnreadableFileError(AppError):
    """422: a damaged, password-protected or text-less (e.g. scanned) file."""

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
    """The file's suffix when we can read it; 415 for video, audio and unknown types."""
    if is_video(filename, content_type):
        raise UnsupportedFileError(VIDEO_DETAIL)
    suffix = file_suffix(filename, content_type)
    if suffix in AUDIO_SUFFIXES:
        raise UnsupportedFileError(AUDIO_DETAIL)
    if suffix not in TEXT_SUFFIXES | CAPTION_SUFFIXES | {".pdf", ".docx"}:
        raise UnsupportedFileError(UNSUPPORTED_DETAIL)
    return suffix


def extract_text(filename: str, content_type: str | None, data: bytes | BinaryIO) -> str:
    """The file's text, normalized; raises 413 / 415 / 422 errors. CPU-bound (PDFs).

    `data` is the file's bytes or a seekable binary stream positioned at its start.
    """
    suffix = supported_suffix(filename, content_type)
    stream = io.BytesIO(data) if isinstance(data, bytes) else data
    if stream_size(stream) > MAX_UPLOAD_BYTES:
        raise ContentTooLargeError(TOO_LARGE_DETAIL)
    if suffix == ".pdf":
        text = pdf_text(stream)
    elif suffix == ".docx":
        text = docx_text(stream)
    elif suffix in CAPTION_SUFFIXES:
        text = caption_text(decode(stream.read(TEXT_READ_LIMIT_BYTES)))
    else:
        text = decode(stream.read(TEXT_READ_LIMIT_BYTES))
    text = normalize(text)
    if not text:
        raise UnreadableFileError(EMPTY_DETAIL)
    return text


def decode(data: bytes) -> str:
    """UTF-8 (with or without BOM), falling back to Windows-1252 for older exports.

    A character cut in half at the end (a text file read only up to
    `TEXT_READ_LIMIT_BYTES`) is dropped instead of forcing the fallback.
    """
    try:
        return codecs.getincrementaldecoder("utf-8-sig")().decode(data, final=False)
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


def pdf_text(stream: BinaryIO) -> str:
    """Text of every page; any parser failure on the untrusted file becomes a 422."""
    try:
        reader = PdfReader(stream)
        encrypted = reader.is_encrypted and not reader.decrypt("")
        pages = [] if encrypted else [page.extract_text() or "" for page in reader.pages]
    except Exception as error:  # any parser failure on an untrusted file
        raise UnreadableFileError(UNREADABLE_DETAIL) from error
    if encrypted:
        raise UnreadableFileError(UNREADABLE_DETAIL)
    return "\n\n".join(pages)


def docx_text(stream: BinaryIO) -> str:
    """Paragraphs, then table rows as "cell | cell" lines (merged cells counted once)."""
    try:
        document = Document(stream)
        parts = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                parts.append(" | ".join(dict.fromkeys(text for text in cells if text)))
    except Exception as error:  # not a zip / not a Word document / malformed XML
        raise UnreadableFileError(UNREADABLE_DETAIL) from error
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
