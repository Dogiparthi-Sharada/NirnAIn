"""Fixtures shared by the step 7 change API tests.

Purpose: Give every change API test an empty in-memory change store and a client whose change
repository and detector are the fakes, so the tests run without a database or Claude.
How it fits: Wraps `ChangeStore` and `use_fake_changes` from tests/support/changes.py for
tests/changes/test_api.py and test_team.py; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.support.changes import ChangeStore, use_fake_changes
from tests.support.store import Store


@pytest.fixture
def change_store() -> ChangeStore:
    """Provide an empty in-memory change store."""
    return ChangeStore()


@pytest.fixture
def change_client(app: FastAPI, store: Store, change_store: ChangeStore) -> Iterator[TestClient]:
    """Provide a test client whose step 7 repository and detector are the fakes."""
    use_fake_changes(app, store, change_store)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
