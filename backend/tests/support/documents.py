"""Document upload helpers: tiny PDF and Word files, a fake document repository and an upload call.

Purpose: Test uploads end to end without real files or a database, with the same RLS outcomes
(readers see, writers add and delete).
How it fits: Used by the tests in tests/documents (whose conftest installs `FakeDocuments`);
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

import docx
from fastapi.testclient import TestClient

from app.ai.documents import (
    Extraction,
)
from app.domain.common import DocumentKind
from app.domain.intake import SourceDocument
from tests.support.clock import NOW
from tests.support.store import ProjectScopedFake, Store


def pdf_with_text(text: str) -> bytes:
    """A minimal one-page PDF that draws `text` in Helvetica (empty text = blank page)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_offset = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_offset,
    )
    return bytes(output)


def docx_bytes() -> bytes:
    """Return a Word file with one paragraph and a one-row table."""
    document = docx.Document()
    document.add_paragraph("Kickoff notes")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Budget"
    table.cell(0, 1).text = "$375K"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- API
@dataclass
class DocumentData:
    """What the fake document table holds: documents and their extracted texts."""

    documents: dict[uuid.UUID, SourceDocument] = field(default_factory=dict)
    texts: dict[uuid.UUID, str | None] = field(default_factory=dict)


class FakeDocuments(ProjectScopedFake):
    """In-memory documents with the RLS rules the API relies on."""

    def __init__(self, store: Store, data: DocumentData, user_id: uuid.UUID) -> None:
        """Act as the given user over the store and the document data."""
        super().__init__(store, user_id)
        self.data = data

    async def create(
        self,
        project_id: uuid.UUID,
        kind: DocumentKind,
        title: str,
        mime_type: str | None,
        size_bytes: int,
        extraction: Extraction,
    ) -> SourceDocument | None:
        """Store an upload's extraction result as a document."""
        document = SourceDocument(
            id=uuid.uuid4(),
            project_id=project_id,
            kind=kind,
            title=title,
            mime_type=mime_type,
            size_bytes=size_bytes,
            extraction_status=extraction.status,
            extraction_error=extraction.error,
            chars=len(extraction.text or ""),
            truncated=extraction.truncated,
            created_at=NOW + timedelta(seconds=len(self.data.documents)),
        )
        self.data.documents[document.id] = document
        self.data.texts[document.id] = extraction.text
        return document

    async def get(self, document_id: uuid.UUID) -> SourceDocument | None:
        """Return a visible document, or None."""
        document = self.data.documents.get(document_id)
        return document if document and self._role(document.project_id) else None

    async def list_for_project(self, project_id: uuid.UUID) -> list[SourceDocument]:
        """Return the project's documents, newest first."""
        rows = [doc for doc in self.data.documents.values() if doc.project_id == project_id]
        return sorted(rows, key=lambda doc: doc.created_at, reverse=True)

    async def delete(self, document_id: uuid.UUID) -> bool:
        """Delete a document; False when it is not there."""
        return self.data.documents.pop(document_id, None) is not None


def upload_file(
    client: TestClient, project_id: str, headers: dict[str, str], name: str, data: bytes, mime: str
) -> dict[str, object]:
    """Upload a file to the project as multipart `file` and return the JSON answer."""
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": (name, data, mime)},
        headers=headers,
    )
    return {"status": response.status_code, **response.json()}
