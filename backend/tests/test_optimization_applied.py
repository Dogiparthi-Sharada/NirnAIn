"""Re-running after a plan is applied (round 2, D3): the plan in force is the baseline and the
inputs sheet tells which inputs hash it was solved from.

What: `POST …/optimization/runs` without `baselineRunId` starts from the applied run (its owners
become the current owners), an explicit `baselineRunId` still wins, and `GET …/optimization/inputs`
returns `appliedHash`. Why: inputs stay editable after Plan vN, so a re-run must move as little
as possible and step 6 must see when the inputs changed. How it fits: reuses the in-memory
fakes of tests/test_optimization_api.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.optimization.model import Employee, PlanningInput, Task
from tests.support.store import Store
from tests.test_optimization_api import Auth, FakeRunner, FakeRuns, RunStore

TASK, MARK, LISA = "task-design", "mark", "lisa"


@pytest.fixture
def run_store() -> RunStore:
    return RunStore()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def applied_client(
    app: FastAPI, store: Store, run_store: RunStore, runner: FakeRunner
) -> Iterator[TestClient]:
    def runs(principal: CurrentPrincipal) -> FakeRuns:
        return FakeRuns(store, run_store, principal.user_id)

    app.dependency_overrides[deps.get_optimization_repo] = runs
    app.dependency_overrides[deps.get_optimization_runner] = lambda: runner
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _project(
    client: TestClient, store: Store, auth: Auth, run_store: RunStore
) -> tuple[str, dict[str, str]]:
    """A ready project (one work package, two people) and its owner's headers."""
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    headers = auth(user)
    created = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "CRM"}, headers=headers)
    project = created.json()["id"]
    run_store.inputs[uuid.UUID(project)] = PlanningInput(
        project,
        "CRM",
        date(2026, 10, 5),
        (Task(TASK, "1.1.1", "Design", 5, owner_id=MARK),),
        (Employee(MARK, "Mark Chen"), Employee(LISA, "Lisa Park")),
    )
    return project, headers


def _apply(run_store: RunStore, run_id: str, owner: str, inputs_hash: str | None) -> None:
    """Mark a solved run as Plan v1, planned with `owner` on the work package."""
    run = run_store.runs[uuid.UUID(run_id)]
    run.update(status="optimal", version=1, applied_at=datetime.now(UTC))
    run["options"] = {**run["options"], "inputsHash": inputs_hash}
    run_store.owners[uuid.UUID(run_id)] = {TASK: owner}


def _snapshot_owner(run_store: RunStore, run_id: str) -> str | None:
    owner: str | None = run_store.runs[uuid.UUID(run_id)]["snapshot"]["tasks"][0]["owner_id"]
    return owner


def test_no_applied_plan_means_no_applied_hash_and_no_baseline(
    applied_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    project, headers = _project(applied_client, store, auth, run_store)
    inputs = applied_client.get(f"/api/v1/projects/{project}/optimization/inputs", headers=headers)
    assert inputs.status_code == 200
    assert inputs.json()["appliedHash"] is None
    run = applied_client.post(f"/api/v1/projects/{project}/optimization/runs", headers=headers)
    assert run.status_code == 202, run.text
    assert _snapshot_owner(run_store, run.json()["id"]) == MARK  # the WBS owner, untouched


def test_rerun_starts_from_the_plan_in_force(
    applied_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    project, headers = _project(applied_client, store, auth, run_store)
    runs_url = f"/api/v1/projects/{project}/optimization/runs"
    first = applied_client.post(runs_url, headers=headers).json()["id"]
    _apply(run_store, first, LISA, "hash-of-plan-v1")

    inputs = applied_client.get(f"/api/v1/projects/{project}/optimization/inputs", headers=headers)
    assert inputs.json()["appliedHash"] == "hash-of-plan-v1"
    assert inputs.json()["hash"] != inputs.json()["appliedHash"]

    rerun = applied_client.post(runs_url, headers=headers)
    assert rerun.status_code == 202, rerun.text
    # Plan v1 gave the package to Lisa: the re-run starts from that, and the body is stored
    # as sent (no baselineRunId added).
    assert _snapshot_owner(run_store, rerun.json()["id"]) == LISA
    assert "baselineRunId" not in rerun.json()["options"]

    run_store.runs[uuid.UUID(rerun.json()["id"])]["status"] = "optimal"
    other = applied_client.post(runs_url, headers=headers).json()["id"]
    run_store.runs[uuid.UUID(other)]["status"] = "optimal"
    run_store.owners[uuid.UUID(other)] = {TASK: MARK}
    explicit = applied_client.post(runs_url, json={"baselineRunId": other}, headers=headers)
    assert _snapshot_owner(run_store, explicit.json()["id"]) == MARK
