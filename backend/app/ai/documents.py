"""Text extraction for documents uploaded to AI intake.

Pure and blocking (pypdf / python-docx parse in Python), so the API runs
`extract_text` with `asyncio.to_thread`. Only the extracted text is kept: there
is no object storage yet, so the original file is discarded after this step.

Business rules:
- the kind comes from the file extension, falling back to the MIME type;
- files may be up to 100 MB; they are read from the request's spooled temp
  file (a stream), never copied into one big bytes object, and only the first
  `TEXT_READ_LIMIT_BYTES` of a text file are read;
- video is rejected (415): people should upload the meeting's transcript;
- audio is accepted but not transcribed yet (stored as a failed extraction);
- WebVTT / SRT captions keep only the spoken words (`app.ai.transcripts`);
- at most `MAX_CHARS` characters of text are kept per document;
- failures are recorded with a fixed, user-facing message. Exception text never
  reaches the user or the server log: a parser error can quote the file. Only the
  exception's class is kept, for the API to log with the document's id, size and kind.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import BinaryIO

from app.ai.transcripts import clean_transcript
from app.domain.common import DocumentKind, JobStatus

# Security rule (SEC-006): pypdf's warnings quote bytes of the file being parsed
# ("invalid pdf header: b'Kicko'"), and uploaded content never goes to the server log.
logging.getLogger("pypdf").setLevel(logging.CRITICAL)

# Matches the `size_bytes` check on public.source_documents (100 MB).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_CHARS = 200_000
# At most MAX_CHARS characters are kept, and caption files are only a few times
# longer than their words, so the first 8 MB of a text file is always enough.
TEXT_READ_LIMIT_BYTES = 8 * 1024 * 1024
TITLE_LIMIT = 300
MIME_TYPE_LIMIT = 200

TOO_LARGE_DETAIL = "Files can be at most 100 MB."
UNSUPPORTED_DETAIL = (
    "Unsupported file type. Upload a PDF, Word (.docx), transcript (.txt, .vtt, .srt) "
    "or audio file."
)
VIDEO_DETAIL = (
    "MP4 and other video files are not supported. Upload the meeting's transcript "
    "(.vtt, .srt or .txt) instead."
)
AUDIO_ERROR = "Audio transcription is not available yet."
NO_TEXT_ERROR = "No text could be read from this file (is it a scanned image?)."
UNREADABLE_ERROR = "This file could not be read."

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".webm", ".aac", ".flac")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv")
KIND_BY_EXTENSION: dict[str, DocumentKind] = {
    ".pdf": DocumentKind.PDF,
    ".docx": DocumentKind.DOCX,
    ".txt": DocumentKind.TRANSCRIPT,
    ".md": DocumentKind.TRANSCRIPT,
    ".vtt": DocumentKind.TRANSCRIPT,
    ".srt": DocumentKind.TRANSCRIPT,
    **dict.fromkeys(AUDIO_EXTENSIONS, DocumentKind.AUDIO),
}
KIND_BY_MIME_TYPE: dict[str, DocumentKind] = {
    "application/pdf": DocumentKind.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocumentKind.DOCX,
    "text/plain": DocumentKind.TRANSCRIPT,
    "text/markdown": DocumentKind.TRANSCRIPT,
    "text/vtt": DocumentKind.TRANSCRIPT,
    "text/srt": DocumentKind.TRANSCRIPT,
    "application/x-subrip": DocumentKind.TRANSCRIPT,
}


@dataclass(frozen=True, slots=True)
class Extraction:
    """Outcome of reading one file: its text, or the user-facing reason it has none."""

    status: JobStatus
    text: str | None = None
    error: str | None = None
    # The parser's exception class when the file couldn't be read: logged, never stored.
    failure: str | None = field(default=None, compare=False)

    @classmethod
    def failed(cls, error: str, failure: str | None = None) -> Extraction:
        """A failed extraction with a fixed, user-facing message (and the exception class)."""
        return cls(JobStatus.FAILED, None, error, failure)


def file_title(filename: str | None) -> str:
    """The file's base name (some browsers send a path), cut to the column limit."""
    base_name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return base_name[:TITLE_LIMIT] or "Untitled document"


def _extension(filename: str | None) -> str:
    """The lower-case extension with its dot ("" when the name has none)."""
    name = (filename or "").lower()
    dot_position = name.rfind(".")
    return name[dot_position:] if dot_position >= 0 else ""


def _mime_type(content_type: str | None) -> str:
    """The bare, lower-case MIME type of a Content-Type header value."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def is_video(filename: str | None, content_type: str | None) -> bool:
    """A video file: a video extension, any video/* MIME type, or a .webm that isn't audio/webm.

    .webm holds audio or video; only an explicit audio/webm MIME type marks it as audio.
    """
    extension = _extension(filename)
    mime_type = _mime_type(content_type)
    if extension in VIDEO_EXTENSIONS or mime_type.startswith("video/"):
        return True
    return extension == ".webm" and mime_type != "audio/webm"


def detect_kind(filename: str | None, content_type: str | None) -> DocumentKind | None:
    """Kind from the file extension, falling back to the MIME type; None when unsupported."""
    extension = _extension(filename)
    if extension in KIND_BY_EXTENSION:
        return KIND_BY_EXTENSION[extension]
    mime_type = _mime_type(content_type)
    if mime_type.startswith("audio/"):
        return DocumentKind.AUDIO
    return KIND_BY_MIME_TYPE.get(mime_type)


def normalize(text: str) -> str:
    """Make extracted text storable and compact, capped at `MAX_CHARS`.

    Postgres text can't hold NUL characters; line endings are unified, trailing
    spaces and runs of blank lines removed.
    """
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_CHARS]


def stream_size(stream: BinaryIO) -> int:
    """Bytes in a seekable stream (blocking for on-disk files); leaves it rewound."""
    size = stream.seek(0, io.SEEK_END)
    stream.seek(0)
    return size


def _pdf_text(stream: BinaryIO) -> str:
    from pypdf import PdfReader  # noqa: PLC0415 - heavy import, only needed for PDFs

    reader = PdfReader(stream)
    page_texts: list[str] = []
    collected_chars = 0
    for page in reader.pages:
        page_text = page.extract_text() or ""
        page_texts.append(page_text)
        collected_chars += len(page_text)
        if collected_chars >= MAX_CHARS:  # the rest would be cut anyway
            break
    return "\n\n".join(page_texts)


def _docx_text(stream: BinaryIO) -> str:
    import docx  # noqa: PLC0415 - heavy import, only needed for Word files

    document = docx.Document(stream)
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cell_texts: list[str] = []
            for cell in row.cells:
                cell_text = cell.text.strip()
                if cell_text and cell_text not in cell_texts:  # merged cells repeat their text
                    cell_texts.append(cell_text)
            if cell_texts:
                parts.append(" | ".join(cell_texts))
    return "\n".join(parts)


def extract_text(kind: DocumentKind, data: bytes | BinaryIO) -> Extraction:
    """Read the text of an uploaded file (blocking: call it in a worker thread).

    `data` is the file's bytes or a seekable binary stream positioned at its start.
    """
    if kind == DocumentKind.AUDIO:
        return Extraction.failed(AUDIO_ERROR)
    stream = io.BytesIO(data) if isinstance(data, bytes) else data
    try:
        if kind == DocumentKind.PDF:
            raw_text = _pdf_text(stream)
        elif kind == DocumentKind.DOCX:
            raw_text = _docx_text(stream)
        else:
            raw_bytes = stream.read(TEXT_READ_LIMIT_BYTES)
            raw_text = clean_transcript(raw_bytes.decode("utf-8", errors="replace"))
    except Exception as exc:
        # Corrupt or malicious files. Security rule (SEC-006): the exception's text can
        # quote the file, so only its class is kept; the user sees a fixed message.
        return Extraction.failed(UNREADABLE_ERROR, type(exc).__name__)
    text = normalize(raw_text)
    if not text:
        return Extraction.failed(NO_TEXT_ERROR)
    return Extraction(JobStatus.SUCCEEDED, text)
