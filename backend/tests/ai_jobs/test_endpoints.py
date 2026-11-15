"""Tests of the AI job endpoints: queueing WBS generation and validation, conflicts and access.

Purpose: Prove a request queues exactly one job, refuses a second one or a generation over an
existing WBS (409), fails an orphaned job, and keeps viewers (403) and outsiders (404) out.
How it fits: Runs app/api/v1/ai.py against the fakes of tests/ai_jobs/conftest.py; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.common import OrgRole
from app.jobs.ai_jobs import (
    HAS_WBS_ERROR,
    RESTART_ERROR,
)
from app.repositories.ai_jobs import ActiveJob
from tests.support.ai_jobs import JobData
from tests.support.projects import create_project
from tests.support.runners import FakeRunner
from tests.support.store import Store
from tests.support.tokens import Auth


def test_generate_and_validate_queue_a_job(
    jobs_client: TestClient, store: Store, auth: Auth, runner: FakeRunner
) -> None:
    """Generate and validate each queue a job (202), submit it and show it as the latest job."""
    user, _, pid = create_project(jobs_client, store, auth)
    for kind, path in (("wbs_generate", "generate"), ("wbs_validate", "validate")):
        response = jobs_client.post(
            f"/api/v1/projects/{pid}/wbs/{path}", json={}, headers=auth(user)
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert (body["kind"], body["status"], body["projectId"]) == (kind, "queued", pid)
        assert body["id"] == str(runner.submitted[-1])
        latest = jobs_client.get(
            f"/api/v1/projects/{pid}/ai-jobs/latest?kind={kind}", headers=auth(user)
        )
        assert latest.json()["id"] == body["id"]


def test_generate_conflicts(
    jobs_client: TestClient, store: Store, auth: Auth, job_data: JobData, runner: FakeRunner
) -> None:
    """A WBS, a running job or a lost race make generation 409; a stale job is failed first."""
    user, _, pid = create_project(jobs_client, store, auth)
    url = f"/api/v1/projects/{pid}/wbs/generate"
    job_data.has_wbs = True
    response = jobs_client.post(url, headers=auth(user))
    assert response.status_code == 409
    assert response.json()["detail"] == HAS_WBS_ERROR
    # Validation doesn't care whether a WBS exists.
    assert (
        jobs_client.post(f"/api/v1/projects/{pid}/wbs/validate", headers=auth(user)).status_code
        == 202
    )

    job_data.has_wbs = False
    job_data.active = ActiveJob(id=uuid.uuid4(), stale=False)
    response = jobs_client.post(url, headers=auth(user))
    assert response.status_code == 409
    assert response.json()["detail"] == "WBS generation is already running for this project."

    stale = uuid.uuid4()
    job_data.active = ActiveJob(id=stale, stale=True)
    assert jobs_client.post(url, headers=auth(user)).status_code == 202
    assert runner.failed == [(stale, RESTART_ERROR)]

    job_data.active, job_data.race = None, True
    assert jobs_client.post(url, headers=auth(user)).status_code == 409


def test_ai_job_permissions_and_missing(jobs_client: TestClient, store: Store, auth: Auth) -> None:
    """Viewers get 403, outsiders 404; latest job: 404 when there is none, 422 for a bad kind."""
    user, org, pid = create_project(jobs_client, store, auth)
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    stranger = store.add_user("Eve")
    url = f"/api/v1/projects/{pid}/wbs/generate"
    assert jobs_client.post(url, headers=auth(viewer)).status_code == 403
    assert jobs_client.post(url, headers=auth(stranger)).status_code == 404
    latest = f"/api/v1/projects/{pid}/ai-jobs/latest"
    assert jobs_client.get(f"{latest}?kind=wbs_validate", headers=auth(user)).status_code == 404
    assert jobs_client.get(f"{latest}?kind=nope", headers=auth(user)).status_code == 422
    assert jobs_client.get(latest, headers=auth(user)).status_code == 422
