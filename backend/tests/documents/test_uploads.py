"""Tests of the document upload endpoints and the document repository.

Purpose: Prove uploads are stored as text, listed newest first and deleted; unsupported, video and
oversized files are refused early; viewers and outsiders are kept out; and parser errors never leak.
How it fits: Exercises app/api/v1/documents.py with the fake repository and
app/repositories/documents.py with a fake connection; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message

from app.ai import documents as extraction_module
from app.ai.documents import (
    AUDIO_ERROR,
    TOO_LARGE_DETAIL,
    UNREADABLE_ERROR,
    UNSUPPORTED_DETAIL,
    VIDEO_DETAIL,
    Extraction,
    extract_text,
)
from app.api.v1 import documents as documents_api
from app.core.errors import ContentTooLargeError
from app.domain.common import DocumentKind, JobStatus, OrgRole
from app.repositories.documents import DocumentRepository
from tests.support.connections import Conn, Rows
from tests.support.documents import (
    NOW,
    DocumentData,
    pdf_with_text,
    upload_file,
)
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth


def test_upload_list_and_delete(
    documents_client: TestClient, store: Store, document_data: DocumentData, auth: Auth
) -> None:
    """An upload is stored with its text details, listed newest first and deleted (then gone)."""
    user, _, project_id = create_project(documents_client, store, auth)
    brief = upload_file(
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

    call = upload_file(documents_client, project_id, auth(user), "call.mp3", b"ID3", "audio/mpeg")
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
    """Unsupported files and video get 415, audio is kept (201), a file over the limit 413."""
    user, _, project_id = create_project(documents_client, store, auth)
    sheet = upload_file(documents_client, project_id, auth(user), "plan.xlsx", b"PK", "x/y")
    assert (sheet["status"], sheet["detail"]) == (415, UNSUPPORTED_DETAIL)
    movie = upload_file(documents_client, project_id, auth(user), "call.mp4", b"\x00", "video/mp4")
    assert (movie["status"], movie["detail"]) == (415, VIDEO_DETAIL)
    voice = upload_file(
        documents_client, project_id, auth(user), "memo.webm", b"\x1a", "audio/webm"
    )
    assert (voice["status"], voice["kind"]) == (201, "audio")
    monkeypatch.setattr(documents_api, "MAX_UPLOAD_BYTES", 4)
    big = upload_file(documents_client, project_id, auth(user), "notes.txt", b"12345", "text/plain")
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
    """A declared or streamed body over the limit is refused with 413 before it is read."""
    user, _, project_id = create_project(documents_client, store, auth)
    monkeypatch.setattr(documents_api, "MAX_UPLOAD_BYTES", 100)
    monkeypatch.setattr(documents_api, "FORM_OVERHEAD_BYTES", 200)
    url = f"/api/v1/projects/{project_id}/documents"
    declared = upload_file(
        documents_client, project_id, auth(user), "a.txt", b"x" * 400, "text/plain"
    )
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
    """The capped receive passes messages through and raises 413 once the limit is passed."""
    messages: list[Message] = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True} for _ in range(5)
    ]
    pulled: list[Message] = []

    async def receive() -> Message:
        """Return the next scripted ASGI message, remembering what was pulled."""
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
    """Viewers can't upload or delete (403) and outsiders can't see the project (404)."""
    user, org, project_id = create_project(documents_client, store, auth)
    notes = upload_file(documents_client, project_id, auth(user), "a.txt", b"Notes", "text/plain")
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    stranger = store.add_user("Eve")

    as_viewer = upload_file(documents_client, project_id, auth(viewer), "b.txt", b"x", "text/plain")
    assert as_viewer["status"] == 403
    as_stranger = upload_file(
        documents_client, project_id, auth(stranger), "b.txt", b"x", "text/plain"
    )
    assert as_stranger["status"] == 404
    listing = documents_client.get(
        f"/api/v1/projects/{project_id}/documents", headers=auth(stranger)
    )
    assert listing.status_code == 404
    url = f"/api/v1/documents/{notes['id']}"
    assert documents_client.delete(url, headers=auth(viewer)).status_code == 403
    assert documents_client.delete(url, headers=auth(stranger)).status_code == 404


def test_extraction_errors_never_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpected parser error becomes the fixed "couldn't read" failure."""

    def explode(data: bytes) -> str:
        """Raise an error whose message must not reach the user."""
        raise RuntimeError("secret internals")

    monkeypatch.setattr(extraction_module, "_pdf_text", explode)
    result = extract_text(DocumentKind.PDF, b"%PDF")
    assert result == Extraction.failed(UNREADABLE_ERROR)


# --------------------------------------------------------------------------- repository SQL
def _document_row(**overrides: object) -> dict[str, object]:
    """Return a stored document row with the given overrides."""
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
    """The repository stores an upload's text; lists and deletes only uploads, never notes."""
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
