"""Uploads longer than the extraction limit say so (INTAKE-020).

What: a text, PDF or Word file with more text than `MAX_CHARS` keeps its first part, is
flagged `truncated`, and the upload response says "Only the first N characters were read".
Why: QA's pack 2 found a 300,000-character transcript cut at 200,000 without a word, so a
decision after the limit never reached review.
How it fits: the upload runs against the in-memory documents of test_documents.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from app.ai import documents as extraction_module
from app.ai.documents import MAX_CHARS, extract_text
from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import DocumentKind
from tests.conftest import Store
from tests.test_documents import (
    Auth,
    DocumentData,
    FakeDocuments,
    _project,
    _upload,
    pdf_with_text,
)


@pytest.fixture
def documents_client(app: FastAPI, store: Store) -> Iterator[TestClient]:
    data = DocumentData()

    def documents(principal: CurrentPrincipal) -> FakeDocuments:
        return FakeDocuments(store, data, principal.user_id)

    app.dependency_overrides[deps.get_document_repo] = documents
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_a_long_upload_says_only_its_first_part_was_read(
    documents_client: TestClient, store: Store, auth: Auth
) -> None:
    user, _, project_id = _project(documents_client, store, auth)
    transcript = ("Status update. " * 20_000).encode()  # 300,000 characters

    long_one = _upload(
        documents_client, project_id, auth(user), "long.txt", transcript, "text/plain"
    )
    short_one = _upload(
        documents_client, project_id, auth(user), "short.txt", b"Budget $410,000", "text/plain"
    )

    assert (long_one["chars"], long_one["truncated"], long_one["extractionNote"]) == (
        MAX_CHARS,
        True,
        "Only the first 200,000 characters were read.",
    )
    assert (short_one["truncated"], short_one["extractionNote"]) == (False, None)
    listed = documents_client.get(f"/api/v1/projects/{project_id}/documents", headers=auth(user))
    assert [document["truncated"] for document in listed.json()] == [False, True]


def test_unread_pdf_pages_and_long_text_files_count_as_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = PdfWriter()
    for text in ("First page", "Second page"):
        writer.add_page(PdfReader(io.BytesIO(pdf_with_text(text))).pages[0])
    two_pages = io.BytesIO()
    writer.write(two_pages)
    monkeypatch.setattr(extraction_module, "MAX_CHARS", 10)

    first_page_only = extract_text(DocumentKind.PDF, two_pages.getvalue())
    assert (first_page_only.text, first_page_only.truncated) == ("First page", True)

    monkeypatch.setattr(extraction_module, "TEXT_READ_LIMIT_BYTES", 4)
    cut_file = extract_text(DocumentKind.TRANSCRIPT, b"Go-live")
    assert (cut_file.text, cut_file.truncated) == ("Go-l", True)
    whole_file = extract_text(DocumentKind.TRANSCRIPT, b"Go")
    assert (whole_file.text, whole_file.truncated) == ("Go", False)
