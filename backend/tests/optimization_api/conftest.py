"""Fixtures of the optimization endpoint tests.

Purpose: Run the optimization endpoints against the fake run repository and a recording runner.
How it fits: Used by tests/optimization_api/test_runs.py and test_run_inputs.py; fakes come from
tests/support/optimization_runs.py; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from tests.support.optimization_runs import (
    FakeRuns,
    RunStore,
)
from tests.support.runners import FakeRunner
from tests.support.store import Store


@pytest.fixture
def run_store() -> RunStore:
    """Provide an empty fake run store."""
    return RunStore()


@pytest.fixture
def runner() -> FakeRunner:
    """Provide a runner that records submitted runs."""
    return FakeRunner()


@pytest.fixture
def opt_client(
    app: FastAPI, store: Store, run_store: RunStore, runner: FakeRunner
) -> Iterator[TestClient]:
    """Provide a test client whose optimization repository and runner are fakes."""

    def runs(principal: CurrentPrincipal) -> FakeRuns:
        """Return the fake run repository acting as the caller."""
        return FakeRuns(store, run_store, principal.user_id)

    app.dependency_overrides[deps.get_optimization_repo] = runs
    app.dependency_overrides[deps.get_optimization_runner] = lambda: runner
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
