"""Text extraction for documents uploaded to AI intake.

Pure and blocking (pypdf / python-docx parse in Python), so the API runs
`extract_text` with `asyncio.to_thread`. Only the extracted text is kept: there
is no object storage yet, so the original file is discarded after this step.

Business rules:
- the kind comes from the file extension, falling back to the MIME type;
- audio is accepted but not transcribed yet (stored as a failed extraction);
- WebVTT / SRT captions keep only the spoken words (`app.ai.transcripts`);
- at most `MAX_CHARS` characters of text are kept per document;
- failures are recorded with a fixed, user-facing message. Exception text never
  reaches the user: a parser error can contain file internals.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

from app.ai.transcripts import clean_transcript
from app.domain.common import DocumentKind, JobStatus

logger = logging.getLogger("app.ai.documents")

# Matches the `size_bytes` check on public.source_documents (50 MB).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_CHARS = 200_000
TITLE_LIMIT = 300
MIME_TYPE_LIMIT = 200

TOO_LARGE_DETAIL = "Files can be at most 50 MB."
UNSUPPORTED_DETAIL = (
    "Unsupported file type. Upload a PDF, Word (.docx), transcript (.txt, .vtt, .srt) "
    "or audio file."
)
AUDIO_ERROR = "Audio transcription is not available yet."
NO_TEXT_ERROR = "No text could be read from this file (is it a scanned image?)."
UNREADABLE_ERROR = "This file could not be read."

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".webm", ".aac", ".flac")
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

    @classmethod
    def failed(cls, error: str) -> Extraction:
        """A failed extraction with a fixed, user-facing message."""
        return cls(JobStatus.FAILED, None, error)


def file_title(filename: str | None) -> str:
    """The file's base name (some browsers send a path), cut to the column limit."""
    base_name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return base_name[:TITLE_LIMIT] or "Untitled document"


def detect_kind(filename: str | None, content_type: str | None) -> DocumentKind | None:
    """Kind from the file extension, falling back to the MIME type; None when unsupported."""
    name = (filename or "").lower()
    dot_position = name.rfind(".")
    if dot_position >= 0 and name[dot_position:] in KIND_BY_EXTENSION:
        return KIND_BY_EXTENSION[name[dot_position:]]
    mime_type = (content_type or "").split(";", 1)[0].strip().lower()
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


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader  # noqa: PLC0415 - heavy import, only needed for PDFs

    reader = PdfReader(io.BytesIO(data))
    page_texts: list[str] = []
    collected_chars = 0
    for page in reader.pages:
        page_text = page.extract_text() or ""
        page_texts.append(page_text)
        collected_chars += len(page_text)
        if collected_chars >= MAX_CHARS:  # the rest would be cut anyway
            break
    return "\n\n".join(page_texts)


def _docx_text(data: bytes) -> str:
    import docx  # noqa: PLC0415 - heavy import, only needed for Word files

    document = docx.Document(io.BytesIO(data))
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


def extract_text(kind: DocumentKind, data: bytes) -> Extraction:
    """Read the text of an uploaded file (blocking: call it in a worker thread)."""
    if kind == DocumentKind.AUDIO:
        return Extraction.failed(AUDIO_ERROR)
    try:
        if kind == DocumentKind.PDF:
            raw_text = _pdf_text(data)
        elif kind == DocumentKind.DOCX:
            raw_text = _docx_text(data)
        else:
            raw_text = clean_transcript(data.decode("utf-8", errors="replace"))
    except Exception:
        # Corrupt or malicious files: log the details, show only a fixed message.
        logger.warning("document text extraction failed", exc_info=True, extra={"kind": kind})
        return Extraction.failed(UNREADABLE_ERROR)
    text = normalize(raw_text)
    if not text:
        return Extraction.failed(NO_TEXT_ERROR)
    return Extraction(JobStatus.SUCCEEDED, text)
