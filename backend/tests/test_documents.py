"""Document uploads: text extraction, the upload/list/delete API and its SQL (no database)."""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import docx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message

from app.ai import documents as extraction_module
from app.ai.documents import (
    AUDIO_ERROR,
    MAX_CHARS,
    NO_TEXT_ERROR,
    TOO_LARGE_DETAIL,
    UNREADABLE_ERROR,
    UNSUPPORTED_DETAIL,
    VIDEO_DETAIL,
    Extraction,
    clean_transcript,
    detect_kind,
    extract_text,
    file_title,
    is_video,
)
from app.api import deps
from app.api.v1 import documents as documents_api
from app.core.errors import ContentTooLargeError
from app.core.security import CurrentPrincipal
from app.domain.common import DocumentKind, JobStatus, OrgRole
from app.domain.intake import SourceDocument
from app.repositories.documents import DocumentRepository
from tests.conftest import Store
from tests.test_optimization_repo import Rows
from tests.test_repositories import Conn

Auth = Callable[[uuid.UUID], dict[str, str]]
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


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
    document = docx.Document()
    document.add_paragraph("Kickoff notes")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Budget"
    table.cell(0, 1).text = "$375K"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


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


# --------------------------------------------------------------------------- API
@dataclass
class DocumentData:
    documents: dict[uuid.UUID, SourceDocument] = field(default_factory=dict)
    texts: dict[uuid.UUID, str | None] = field(default_factory=dict)


class FakeDocuments:
    """In-memory documents with the RLS rules the API relies on."""

    def __init__(self, store: Store, data: DocumentData, user_id: uuid.UUID) -> None:
        self.store, self.data, self.user_id = store, data, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        project = self.store.projects.get(project_id)
        return self.store.role(project.org_id, self.user_id) if project else None

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def create(
        self,
        project_id: uuid.UUID,
        kind: DocumentKind,
        title: str,
        mime_type: str | None,
        size_bytes: int,
        extraction: Extraction,
    ) -> SourceDocument | None:
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
        document = self.data.documents.get(document_id)
        return document if document and self._role(document.project_id) else None

    async def list_for_project(self, project_id: uuid.UUID) -> list[SourceDocument]:
        rows = [doc for doc in self.data.documents.values() if doc.project_id == project_id]
        return sorted(rows, key=lambda doc: doc.created_at, reverse=True)

    async def delete(self, document_id: uuid.UUID) -> bool:
        return self.data.documents.pop(document_id, None) is not None


@pytest.fixture
def document_data() -> DocumentData:
    return DocumentData()


@pytest.fixture
def documents_client(
    app: FastAPI, store: Store, document_data: DocumentData
) -> Iterator[TestClient]:
    def documents(principal: CurrentPrincipal) -> FakeDocuments:
        return FakeDocuments(store, document_data, principal.user_id)

    app.dependency_overrides[deps.get_document_repo] = documents
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID, str]:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    response = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "CRM"}, headers=auth(user))
    return user, org, response.json()["id"]


def _upload(
    client: TestClient, project_id: str, headers: dict[str, str], name: str, data: bytes, mime: str
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": (name, data, mime)},
        headers=headers,
    )
    return {"status": response.status_code, **response.json()}


def test_upload_list_and_delete(
    documents_client: TestClient, store: Store, document_data: DocumentData, auth: Auth
) -> None:
    user, _, project_id = _project(documents_client, store, auth)
    brief = _upload(
        documents_client, project_id, auth(user), "brief.pdf", pdf_with_text("Scope"), "x/y"
    )
    assert brief["status"] == 201
    assert {key: brief[key] for key in ("kind", "title", "mimeType", "sizeBytes", "chars")} == {
        "kind": "pdf",
        "title": "brief.pdf",
        "mimeType": "x/y",
        "sizeBytes": len(pdf_with_text("Scope")),
        "chars": 5,
    }
    assert (brief["extractionStatus"], brief["extractionError"]) == ("succeeded", None)
    assert set(brief) == {
        "status",
        "id",
        "projectId",
        "kind",
        "title",
        "mimeType",
        "sizeBytes",
        "extractionStatus",
        "extractionError",
        "chars",
        "truncated",
        "extractionNote",
        "createdAt",
    }

    call = _upload(documents_client, project_id, auth(user), "call.mp3", b"ID3", "audio/mpeg")
    assert (call["status"], call["kind"], call["extractionStatus"], call["extractionError"]) == (
        201,
        "audio",
        "failed",
        AUDIO_ERROR,
    )

    listed = documents_client.get(f"/api/v1/projects/{project_id}/documents", headers=auth(user))
    assert [doc["title"] for doc in listed.json()] == ["call.mp3", "brief.pdf"]

    deleted = documents_client.delete(f"/api/v1/documents/{call['id']}", headers=auth(user))
    assert deleted.status_code == 204
    again = documents_client.delete(f"/api/v1/documents/{call['id']}", headers=auth(user))
    assert again.status_code == 404
    assert next(iter(document_data.texts.values())) == "Scope"


def test_upload_rejects_unsupported_and_too_large_files(
    documents_client: TestClient, store: Store, auth: Auth, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _, project_id = _project(documents_client, store, auth)
    sheet = _upload(documents_client, project_id, auth(user), "plan.xlsx", b"PK", "x/y")
    assert (sheet["status"], sheet["detail"]) == (415, UNSUPPORTED_DETAIL)
    movie = _upload(documents_client, project_id, auth(user), "call.mp4", b"\x00", "video/mp4")
    assert (movie["status"], movie["detail"]) == (415, VIDEO_DETAIL)
    voice = _upload(documents_client, project_id, auth(user), "memo.webm", b"\x1a", "audio/webm")
    assert (voice["status"], voice["kind"]) == (201, "audio")
    monkeypatch.setattr(documents_api, "MAX_UPLOAD_BYTES", 4)
    big = _upload(documents_client, project_id, auth(user), "notes.txt", b"12345", "text/plain")
    assert (big["status"], big["detail"]) == (413, TOO_LARGE_DETAIL)
    missing = documents_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"other": ("a.txt", b"x", "text/plain")},
        headers=auth(user),
    )
    assert missing.status_code == 422


def test_oversized_uploads_are_refused_before_they_are_read(
    documents_client: TestClient, store: Store, auth: Auth, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _, project_id = _project(documents_client, store, auth)
    monkeypatch.setattr(documents_api, "MAX_UPLOAD_BYTES", 100)
    monkeypatch.setattr(documents_api, "FORM_OVERHEAD_BYTES", 200)
    url = f"/api/v1/projects/{project_id}/documents"
    declared = _upload(documents_client, project_id, auth(user), "a.txt", b"x" * 400, "text/plain")
    assert (declared["status"], declared["detail"]) == (413, TOO_LARGE_DETAIL)

    boundary = "nirnain-test"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode()
    chunks = [head, *([b"x" * 100] * 10), f"\r\n--{boundary}--\r\n".encode()]
    streamed = documents_client.post(  # chunked, no Content-Length: counted as it streams in
        url,
        content=iter(chunks),
        headers={**auth(user), "content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert (streamed.status_code, streamed.json()["detail"]) == (413, TOO_LARGE_DETAIL)


async def test_body_counter_stops_reading_at_the_limit() -> None:
    messages: list[Message] = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True} for _ in range(5)
    ]
    pulled: list[Message] = []

    async def receive() -> Message:
        pulled.append(messages[len(pulled)])
        return pulled[-1]

    receive_within_limit = documents_api._capped_receive(receive, 100)
    assert await receive_within_limit() == messages[0]
    with pytest.raises(ContentTooLargeError):
        await receive_within_limit()
    assert len(pulled) == 2  # the rest of the body is never pulled in


def test_upload_and_delete_permissions(
    documents_client: TestClient, store: Store, auth: Auth
) -> None:
    user, org, project_id = _project(documents_client, store, auth)
    notes = _upload(documents_client, project_id, auth(user), "a.txt", b"Notes", "text/plain")
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    stranger = store.add_user("Eve")

    as_viewer = _upload(documents_client, project_id, auth(viewer), "b.txt", b"x", "text/plain")
    assert as_viewer["status"] == 403
    as_stranger = _upload(documents_client, project_id, auth(stranger), "b.txt", b"x", "text/plain")
    assert as_stranger["status"] == 404
    listing = documents_client.get(
        f"/api/v1/projects/{project_id}/documents", headers=auth(stranger)
    )
    assert listing.status_code == 404
    url = f"/api/v1/documents/{notes['id']}"
    assert documents_client.delete(url, headers=auth(viewer)).status_code == 403
    assert documents_client.delete(url, headers=auth(stranger)).status_code == 404


def test_extraction_errors_never_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(data: bytes) -> str:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(extraction_module, "_pdf_text", explode)
    result = extract_text(DocumentKind.PDF, b"%PDF")
    assert result == Extraction.failed(UNREADABLE_ERROR)


# --------------------------------------------------------------------------- repository SQL
def _document_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "kind": "pdf",
        "title": "brief.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 10,
        "extraction_status": "succeeded",
        "extraction_error": None,
        "chars": 5,
        "truncated": False,
        "created_at": NOW,
    }
    row.update(overrides)
    return row


async def test_repository_stores_uploads_and_hides_pasted_notes() -> None:
    conn = Conn(Rows([]), Rows([_document_row()]))
    repository = DocumentRepository(conn)  # type: ignore[arg-type]
    extraction = Extraction(JobStatus.SUCCEEDED, "Scope")
    document = await repository.create(
        uuid.uuid4(), DocumentKind.PDF, "brief.pdf", "application/pdf", 10, extraction
    )
    assert document is not None
    assert document.chars == 5
    insert, select = conn.sql
    assert insert.startswith("INSERT INTO public.source_documents")
    assert conn.params[0]["content_text"] == "Scope"
    assert conn.params[0]["content_truncated"] is False
    assert conn.params[0]["extraction_status"] == JobStatus.SUCCEEDED
    assert "char_length(public.source_documents.content_text)" in select
    assert "public.source_documents.content_truncated AS truncated" in select
    assert "public.source_documents.kind != " in select

    listing = Conn(Rows([_document_row(), _document_row()]))
    assert len(await DocumentRepository(listing).list_for_project(uuid.uuid4())) == 2  # type: ignore[arg-type]
    assert "ORDER BY public.source_documents.created_at DESC" in listing.sql[0]
    assert listing.params[0]["kind_1"] == DocumentKind.TEXT

    deleting = Conn(Rows([]))
    assert not await DocumentRepository(deleting).delete(uuid.uuid4())  # type: ignore[arg-type]
    assert deleting.sql[0].startswith("DELETE FROM public.source_documents")
    assert "public.source_documents.kind != " in deleting.sql[0]
