"""No uploaded or user-written content in the server log (SEC-006).

What: a file that can't be read is logged with its document id, size, kind and the parser's
exception class only; pypdf's own warnings (which quote the file) are silenced; a failed AI
draft logs the error class only.
Why: QA's pack 2 found a corrupt PDF's text, fake secrets included, in a WARNING traceback.
How it fits: the upload runs against the in-memory documents of test_documents.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.documents import UNREADABLE_ERROR, extract_text
from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import DocumentKind
from app.drafts.reader import DraftReader
from tests.support.store import Store
from tests.test_documents import Auth, DocumentData, FakeDocuments, _project, _upload
from tests.test_drafts import FakeLLM

SECRET = "Hunter2-QA-FAKE"
CORRUPT_PDF = f"Kickoff notes. Admin password: {SECRET}\n%PDF-1.4 1 0 obj << /Length x >>".encode()


@pytest.fixture
def documents_client(app: FastAPI, store: Store) -> Iterator[TestClient]:
    data = DocumentData()

    def documents(principal: CurrentPrincipal) -> FakeDocuments:
        return FakeDocuments(store, data, principal.user_id)

    app.dependency_overrides[deps.get_document_repo] = documents
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    """Everything the captured records would write, tracebacks included."""
    formatter = logging.Formatter()
    return "\n".join(formatter.format(record) + str(record.__dict__) for record in caplog.records)


def test_a_corrupt_upload_is_logged_without_its_content(
    documents_client: TestClient, store: Store, auth: Auth, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    user, _, project_id = _project(documents_client, store, auth)

    upload = _upload(
        documents_client, project_id, auth(user), "secret-plan.pdf", CORRUPT_PDF, "application/pdf"
    )

    assert (upload["status"], upload["extractionError"]) == (201, UNREADABLE_ERROR)
    [record] = [record for record in caplog.records if record.name == "app.api.documents"]
    assert record.getMessage() == "document text extraction failed"
    assert (record.document_id, record.size_bytes, record.kind) == (
        upload["id"],
        len(CORRUPT_PDF),
        "pdf",
    )
    assert record.error.endswith("Error")
    logged = _logged(caplog)
    assert SECRET not in logged
    assert "Kicko" not in logged  # pypdf's header warning quotes the first bytes
    assert "secret-plan" not in logged
    assert record.exc_info is None


def test_the_parser_failure_is_kept_as_a_class_name_only() -> None:
    result = extract_text(DocumentKind.PDF, CORRUPT_PDF)
    assert (result.error, result.text) == (UNREADABLE_ERROR, None)
    assert result.failure is not None
    assert SECRET not in result.failure


async def test_a_failed_ai_draft_logs_the_error_class_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    reader = DraftReader(FakeLLM(ValueError(f"could not parse: {SECRET}")), 5)

    draft = await reader.read(f"Budget notes {SECRET}", "notes.txt")

    assert draft.model == "rules"
    [record] = [record for record in caplog.records if record.name == "app.drafts"]
    assert (record.error, record.exc_info) == ("ValueError", None)
    assert SECRET not in _logged(caplog)
