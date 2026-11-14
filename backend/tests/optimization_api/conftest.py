"""Moved test support."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from tests.support.optimization_runs import (
    FakeRunner,
    FakeRuns,
    RunStore,
)
from tests.support.store import Store


@pytest.fixture
def run_store() -> RunStore:
    return RunStore()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def opt_client(
    app: FastAPI, store: Store, run_store: RunStore, runner: FakeRunner
) -> Iterator[TestClient]:
    def runs(principal: CurrentPrincipal) -> FakeRuns:
        return FakeRuns(store, run_store, principal.user_id)

    app.dependency_overrides[deps.get_optimization_repo] = runs
    app.dependency_overrides[deps.get_optimization_runner] = lambda: runner
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
