"""Moved test support."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from tests.support.ai_jobs import FakeJobs, FakeRunner, JobData
from tests.support.store import Store


@pytest.fixture
def job_data() -> JobData:
    return JobData()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def jobs_client(
    app: FastAPI, store: Store, job_data: JobData, runner: FakeRunner
) -> Iterator[TestClient]:
    def jobs(principal: CurrentPrincipal) -> FakeJobs:
        return FakeJobs(store, job_data, principal.user_id)

    app.dependency_overrides[deps.get_ai_job_repo] = jobs
    app.dependency_overrides[deps.get_ai_runner] = lambda: runner
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
