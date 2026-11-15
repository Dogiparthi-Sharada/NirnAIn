"""Fixtures of the document upload tests.

Purpose: Run the upload endpoints against the fake document repository.
How it fits: Used by every module in tests/documents; fakes come from tests/support/documents.py;
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from tests.support.documents import (
    DocumentData,
    FakeDocuments,
)
from tests.support.store import Store


@pytest.fixture
def document_data() -> DocumentData:
    """Provide empty fake document data."""
    return DocumentData()


@pytest.fixture
def documents_client(
    app: FastAPI, store: Store, document_data: DocumentData
) -> Iterator[TestClient]:
    """Provide a test client whose document repository is the fake."""

    def documents(principal: CurrentPrincipal) -> FakeDocuments:
        """Return the fake document repository acting as the caller."""
        return FakeDocuments(store, document_data, principal.user_id)

    app.dependency_overrides[deps.get_document_repo] = documents
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
