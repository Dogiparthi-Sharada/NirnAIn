"""Tests of queueing and reading optimization runs.

Purpose: Prove a run is queued once (202) and solved in the background, a second or racing request
is a 409, an orphaned run is failed and replaced, access follows the project's roles, and runs read
back camelCase.
How it fits: Exercises app/api/v1/optimization.py with the fakes of
tests/optimization_api/conftest.py; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.domain.common import OrgRole, SolverStatus
from app.jobs.optimizer import RESTART_ERROR
from tests.support.optimization_runs import (
    RUN_KEYS,
    RunStore,
    mark_ready,
    runs_url,
)
from tests.support.projects import create_project
from tests.support.runners import FakeRunner
from tests.support.store import Store
from tests.support.tokens import Auth


def test_create_run_is_202_and_submits(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    """Creating a run answers 202 with the queued run and submits it to the runner."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    response = opt_client.post(runs_url(pid), headers=auth(user))
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == RUN_KEYS
    assert body["status"] == "queued"
    assert body["projectId"] == pid
    assert body["result"] is None
    assert response.headers["location"] == runs_url(pid, f"/{body['id']}")
    assert runner.submitted == [uuid.UUID(body["id"])]
    snapshot = run_store.runs[uuid.UUID(body["id"])]["snapshot"]
    assert snapshot["version"] == 1
    assert snapshot["tasks"][0]["code"] == "1.1.1"


def test_create_run_conflict_when_active(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    """A second run while one is active is a 409 problem and nothing more is submitted."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    assert opt_client.post(runs_url(pid), headers=auth(user)).status_code == 202
    response = opt_client.post(runs_url(pid), headers=auth(user))
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == "An optimization is already running for this project."
    assert len(runner.submitted) == 1


def test_create_run_conflict_on_race(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """A run that loses the insert race to a concurrent request is a 409."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    run_store.race = True
    response = opt_client.post(runs_url(pid), headers=auth(user))
    assert response.status_code == 409
    assert response.json()["detail"] == "An optimization is already running for this project."


def test_stale_active_run_is_failed_and_replaced(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """An orphaned active run is failed with the restart error and a new run is queued."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    first = opt_client.post(runs_url(pid), headers=auth(user)).json()["id"]
    run_store.runs[uuid.UUID(first)]["created_at"] -= timedelta(hours=1)
    response = opt_client.post(runs_url(pid), headers=auth(user))
    assert response.status_code == 202
    old = run_store.runs[uuid.UUID(first)]
    assert (old["status"], old["error"]) == ("failed", RESTART_ERROR)


def test_create_run_permissions(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """Viewers get 403, outsiders and unknown projects 404, requests without a token 401."""
    owner, org, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    response = opt_client.post(runs_url(pid), headers=auth(viewer))
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"

    outsider = store.add_user("Eve Outsider")
    assert opt_client.post(runs_url(pid), headers=auth(outsider)).status_code == 404
    assert opt_client.post(runs_url(str(uuid.uuid4())), headers=auth(owner)).status_code == 404
    assert opt_client.post(runs_url(pid)).status_code == 401


def test_latest_and_get(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """Latest and get return runs to members (viewers too) and 404 to outsiders or for none."""
    user, org, pid = create_project(opt_client, store, auth)
    response = opt_client.get(runs_url(pid, "/latest"), headers=auth(user))
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"

    mark_ready(run_store, pid)
    run_id = opt_client.post(runs_url(pid), headers=auth(user)).json()["id"]
    run = run_store.runs[uuid.UUID(run_id)]
    run.update(
        status=SolverStatus.INFEASIBLE,
        solver="OR-Tools CP-SAT 9.15.6755",
        solve_seconds=1.234,
        iis=[{"name": "Budget cap: $1", "explanation": "Too low."}],
        finished_at=datetime.now(UTC),
    )
    response = opt_client.get(runs_url(pid, "/latest"), headers=auth(user))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == RUN_KEYS
    assert body["id"] == run_id
    assert body["status"] == "infeasible"
    assert body["solveSeconds"] == 1.234
    assert body["iis"] == [{"name": "Budget cap: $1", "explanation": "Too low."}]

    assert opt_client.get(runs_url(pid, f"/{run_id}"), headers=auth(user)).status_code == 200
    assert opt_client.get(runs_url(pid, f"/{uuid.uuid4()}"), headers=auth(user)).status_code == 404
    assert opt_client.get(runs_url(pid, "/not-a-uuid"), headers=auth(user)).status_code == 422

    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert opt_client.get(runs_url(pid, "/latest"), headers=auth(viewer)).status_code == 200
    outsider = store.add_user("Eve")
    assert opt_client.get(runs_url(pid, "/latest"), headers=auth(outsider)).status_code == 404
    assert opt_client.get(runs_url(pid, f"/{run_id}"), headers=auth(outsider)).status_code == 404


def test_finished_run_result_is_camel_case(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """A finished run's result is camelCase and leaves out null KPI sub-lines."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    run_id = opt_client.post(runs_url(pid), headers=auth(user)).json()["id"]
    run_store.runs[uuid.UUID(run_id)].update(
        status="optimal",
        result={
            "kpis": [
                {"label": "Budget", "value": "$1", "sub": "No budget set"},
                {"label": "Solve", "value": "x"},
            ],
            "reassignments": [],
            "scheduleChanges": [
                {
                    "wbs": "1.1.1",
                    "name": "Design",
                    "original": "Wk 1",
                    "optimized": "Wk 1",
                    "delta": "—",
                    "reason": "No change needed",
                }
            ],
            "utilization": [{"name": "Mark Chen", "before": 50, "after": 40}],
        },
    )
    body = opt_client.get(runs_url(pid, f"/{run_id}"), headers=auth(user)).json()
    assert set(body["result"]) == {
        "kpis",
        "reassignments",
        "scheduleChanges",
        "utilization",
        "schedule",
        "summary",
        "remedies",
    }
    assert body["result"]["kpis"][1] == {"label": "Solve", "value": "x"}  # no null `sub`
    assert body["result"]["scheduleChanges"][0]["delta"] == "—"


def test_optimizer_unavailable_is_503() -> None:
    """Without an optimizer on the app, asking for it raises the 503 error."""
    from types import SimpleNamespace

    from app.core.errors import ServiceUnavailableError

    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(ServiceUnavailableError):
        deps.get_optimization_runner(request)


def test_openapi_lists_routes(opt_client: TestClient) -> None:
    """The OpenAPI document lists the optimization routes."""
    paths = opt_client.get("/api/openapi.json").json()["paths"]
    assert "/api/v1/projects/{projectId}/optimization/runs" in paths
    assert "/api/v1/projects/{projectId}/optimization/runs/latest" in paths
    assert "/api/v1/projects/{projectId}/optimization/runs/{runId}" in paths
    assert "/api/v1/projects/{projectId}/optimization/inputs" in paths
