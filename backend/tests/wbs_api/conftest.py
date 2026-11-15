"""Fixtures of the WBS endpoint tests.

Purpose: Run the WBS endpoints against the fake WBS repository.
How it fits: Used by tests/wbs_api/test_nodes.py and test_estimates.py; fakes come from
tests/support/wbs.py; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from tests.support.store import Store
from tests.support.wbs import FakeWbs, WbsStore


@pytest.fixture
def wbs_store() -> WbsStore:
    """Provide empty fake WBS data."""
    return WbsStore()


@pytest.fixture
def wbs_client(app: FastAPI, store: Store, wbs_store: WbsStore) -> Iterator[TestClient]:
    """Provide a test client whose WBS repository is the fake."""

    def repo(principal: CurrentPrincipal) -> FakeWbs:
        """Return the fake WBS repository acting as the caller."""
        return FakeWbs(store, wbs_store, principal.user_id)

    app.dependency_overrides[deps.get_wbs_repo] = repo
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
