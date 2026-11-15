"""Re-running after a plan is applied (round 2, D3): the plan in force is the baseline.

Purpose: Inputs stay editable after Plan vN, so a re-run must move as little as possible and step 6
must see when the inputs changed: a run without `baselineRunId` starts from the applied run (its
owners become the current owners), an explicit `baselineRunId` still wins, and the inputs sheet
returns `appliedHash`.
How it fits: Exercises app/api/v1/optimization.py with the fakes of
tests/optimization_api/conftest.py; layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.optimization.model import Employee, PlanningInput, Task
from tests.support.optimization_runs import RunStore
from tests.support.store import Store
from tests.support.tokens import Auth

TASK, MARK, LISA = "task-design", "mark", "lisa"


def _project(
    client: TestClient, store: Store, auth: Auth, run_store: RunStore
) -> tuple[str, dict[str, str]]:
    """Return a ready project (one work package, two people) and its owner's headers."""
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
    """Mark a run applied as Plan v1 with the given owner and inputs hash."""
    run = run_store.runs[uuid.UUID(run_id)]
    run.update(status="optimal", version=1, applied_at=datetime.now(UTC))
    run["options"] = {**run["options"], "inputsHash": inputs_hash}
    run_store.owners[uuid.UUID(run_id)] = {TASK: owner}


def _snapshot_owner(run_store: RunStore, run_id: str) -> str | None:
    """Return the owner of the first task in a run's frozen input."""
    owner: str | None = run_store.runs[uuid.UUID(run_id)]["snapshot"]["tasks"][0]["owner_id"]
    return owner


def test_no_applied_plan_means_no_applied_hash_and_no_baseline(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """Without an applied plan the sheet has no `appliedHash` and a run keeps the WBS owners."""
    project, headers = _project(opt_client, store, auth, run_store)
    inputs = opt_client.get(f"/api/v1/projects/{project}/optimization/inputs", headers=headers)
    assert inputs.status_code == 200
    assert inputs.json()["appliedHash"] is None
    run = opt_client.post(f"/api/v1/projects/{project}/optimization/runs", headers=headers)
    assert run.status_code == 202, run.text
    assert _snapshot_owner(run_store, run.json()["id"]) == MARK  # the WBS owner, untouched


def test_rerun_starts_from_the_plan_in_force(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """A re-run starts from the applied plan's owners; an explicit baseline run still wins."""
    project, headers = _project(opt_client, store, auth, run_store)
    runs_url = f"/api/v1/projects/{project}/optimization/runs"
    first = opt_client.post(runs_url, headers=headers).json()["id"]
    _apply(run_store, first, LISA, "hash-of-plan-v1")

    inputs = opt_client.get(f"/api/v1/projects/{project}/optimization/inputs", headers=headers)
    assert inputs.json()["appliedHash"] == "hash-of-plan-v1"
    assert inputs.json()["hash"] != inputs.json()["appliedHash"]

    rerun = opt_client.post(runs_url, headers=headers)
    assert rerun.status_code == 202, rerun.text
    # Plan v1 gave the package to Lisa: the re-run starts from that, and the body is stored
    # as sent (no baselineRunId added).
    assert _snapshot_owner(run_store, rerun.json()["id"]) == LISA
    assert "baselineRunId" not in rerun.json()["options"]

    run_store.runs[uuid.UUID(rerun.json()["id"])]["status"] = "optimal"
    other = opt_client.post(runs_url, headers=headers).json()["id"]
    run_store.runs[uuid.UUID(other)]["status"] = "optimal"
    run_store.owners[uuid.UUID(other)] = {TASK: MARK}
    explicit = opt_client.post(runs_url, json={"baselineRunId": other}, headers=headers)
    assert _snapshot_owner(run_store, explicit.json()["id"]) == MARK
