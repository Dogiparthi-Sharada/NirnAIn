"""Optimization endpoints with real JWT verification and in-memory fakes."""

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
    FakeRunner,
    RunStore,
    mark_ready,
    runs_url,
)
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth


def test_create_run_is_202_and_submits(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    r = opt_client.post(runs_url(pid), headers=auth(user))
    assert r.status_code == 202, r.text
    body = r.json()
    assert set(body) == RUN_KEYS
    assert body["status"] == "queued"
    assert body["projectId"] == pid
    assert body["result"] is None
    assert r.headers["location"] == runs_url(pid, f"/{body['id']}")
    assert runner.submitted == [uuid.UUID(body["id"])]
    snapshot = run_store.runs[uuid.UUID(body["id"])]["snapshot"]
    assert snapshot["version"] == 1
    assert snapshot["tasks"][0]["code"] == "1.1.1"


def test_create_run_conflict_when_active(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    assert opt_client.post(runs_url(pid), headers=auth(user)).status_code == 202
    r = opt_client.post(runs_url(pid), headers=auth(user))
    assert r.status_code == 409
    assert r.headers["content-type"] == "application/problem+json"
    assert r.json()["detail"] == "An optimization is already running for this project."
    assert len(runner.submitted) == 1


def test_create_run_conflict_on_race(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    run_store.race = True
    r = opt_client.post(runs_url(pid), headers=auth(user))
    assert r.status_code == 409
    assert r.json()["detail"] == "An optimization is already running for this project."


def test_stale_active_run_is_failed_and_replaced(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    first = opt_client.post(runs_url(pid), headers=auth(user)).json()["id"]
    run_store.runs[uuid.UUID(first)]["created_at"] -= timedelta(hours=1)
    r = opt_client.post(runs_url(pid), headers=auth(user))
    assert r.status_code == 202
    old = run_store.runs[uuid.UUID(first)]
    assert (old["status"], old["error"]) == ("failed", RESTART_ERROR)


def test_create_run_permissions(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    owner, org, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    r = opt_client.post(runs_url(pid), headers=auth(viewer))
    assert r.status_code == 403
    assert r.headers["content-type"] == "application/problem+json"

    outsider = store.add_user("Eve Outsider")
    assert opt_client.post(runs_url(pid), headers=auth(outsider)).status_code == 404
    assert opt_client.post(runs_url(str(uuid.uuid4())), headers=auth(owner)).status_code == 404
    assert opt_client.post(runs_url(pid)).status_code == 401


def test_latest_and_get(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, org, pid = create_project(opt_client, store, auth)
    r = opt_client.get(runs_url(pid, "/latest"), headers=auth(user))
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"

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
    r = opt_client.get(runs_url(pid, "/latest"), headers=auth(user))
    assert r.status_code == 200
    body = r.json()
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
    from types import SimpleNamespace

    from app.core.errors import ServiceUnavailableError

    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(ServiceUnavailableError):
        deps.get_optimization_runner(request)


def test_openapi_lists_routes(opt_client: TestClient) -> None:
    paths = opt_client.get("/api/openapi.json").json()["paths"]
    assert "/api/v1/projects/{projectId}/optimization/runs" in paths
    assert "/api/v1/projects/{projectId}/optimization/runs/latest" in paths
    assert "/api/v1/projects/{projectId}/optimization/runs/{runId}" in paths
    assert "/api/v1/projects/{projectId}/optimization/inputs" in paths
