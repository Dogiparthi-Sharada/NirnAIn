"""Fixtures of the AI job endpoint tests.

Purpose: Run the AI job endpoints against the fake job repository and a recording runner.
How it fits: Used by tests/ai_jobs/test_endpoints.py; fakes come from tests/support; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from tests.support.ai_jobs import FakeJobs, JobData
from tests.support.runners import FakeRunner
from tests.support.store import Store


@pytest.fixture
def job_data() -> JobData:
    """Provide empty fake AI job data."""
    return JobData()


@pytest.fixture
def runner() -> FakeRunner:
    """Provide a runner that records submitted and failed jobs."""
    return FakeRunner()


@pytest.fixture
def jobs_client(
    app: FastAPI, store: Store, job_data: JobData, runner: FakeRunner
) -> Iterator[TestClient]:
    """Provide a test client whose AI job repository and runner are fakes."""

    def jobs(principal: CurrentPrincipal) -> FakeJobs:
        """Return the fake job repository acting as the caller."""
        return FakeJobs(store, job_data, principal.user_id)

    app.dependency_overrides[deps.get_ai_job_repo] = jobs
    app.dependency_overrides[deps.get_ai_runner] = lambda: runner
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
