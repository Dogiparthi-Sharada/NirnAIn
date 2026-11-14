"""Moved test support."""

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
    return PlanStore()


@pytest.fixture
def plan_client(app: FastAPI, store: Store, plan_store: PlanStore) -> Iterator[TestClient]:
    def repo(principal: CurrentPrincipal) -> FakePlanning:
        return FakePlanning(store, plan_store, principal.user_id)

    app.dependency_overrides[deps.get_planning_repo] = repo
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
