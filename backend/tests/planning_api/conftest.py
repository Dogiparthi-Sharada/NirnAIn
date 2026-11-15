"""Fixtures of the planning endpoint tests.

Purpose: Run the planning endpoints against the fake planning repository.
How it fits: Used by every module in tests/planning_api; fakes come from tests/support/planning.py;
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
from tests.support.planning import FakePlanning, PlanStore
from tests.support.store import Store


@pytest.fixture
def plan_store() -> PlanStore:
    """Provide empty fake planning data."""
    return PlanStore()


@pytest.fixture
def plan_client(app: FastAPI, store: Store, plan_store: PlanStore) -> Iterator[TestClient]:
    """Provide a test client whose planning repository is the fake."""

    def repo(principal: CurrentPrincipal) -> FakePlanning:
        """Return the fake planning repository acting as the caller."""
        return FakePlanning(store, plan_store, principal.user_id)

    app.dependency_overrides[deps.get_planning_repo] = repo
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
