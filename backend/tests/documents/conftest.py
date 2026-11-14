"""Moved test support."""

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
