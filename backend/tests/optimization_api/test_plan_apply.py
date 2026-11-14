"""Applying a run on step 6 (round 3, R1: never apply an outdated plan).

What: `POST …/optimization/runs/{runId}/apply` refuses with 409 a run whose recorded
`inputsHash` no longer matches the project's saved inputs, and applies it while they match.
Why: QA's CHANGE-005 solved a plan, cut a person's skills, then applied the old result and it
became the plan in force against the current rules. How it fits: reuses the in-memory fakes
of tests/test_optimization_api.py, plus a plan repository that records what was applied.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.optimization.model import Employee, PlanningInput, Task
from tests.support.optimization_runs import FakeRuns, RunStore
from tests.support.runners import FakeRunner
from tests.support.store import Store
from tests.support.tokens import Auth

STALE = "The inputs changed since this plan was computed. Run the optimizer again before applying."


class FakePlans:
    """`public.apply_optimization_run`: the run becomes the next plan version."""

    def __init__(self, run_store: RunStore) -> None:
        self.run_store = run_store
        self.applied: list[uuid.UUID] = []

    async def apply(self, run_id: uuid.UUID) -> None:
        self.run_store.runs[run_id].update(version=1, applied_at=datetime.now(UTC))
        self.applied.append(run_id)


@pytest.fixture
def run_store() -> RunStore:
    return RunStore()


@pytest.fixture
def plans(run_store: RunStore) -> FakePlans:
    return FakePlans(run_store)


@pytest.fixture
def apply_client(
    app: FastAPI, store: Store, run_store: RunStore, plans: FakePlans
) -> Iterator[TestClient]:
    def runs(principal: CurrentPrincipal) -> FakeRuns:
        return FakeRuns(store, run_store, principal.user_id)

    app.dependency_overrides[deps.get_optimization_repo] = runs
    app.dependency_overrides[deps.get_optimization_runner] = FakeRunner
    app.dependency_overrides[deps.get_plan_repo] = lambda: plans
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _solved_run(
    client: TestClient, store: Store, auth: Auth, run_store: RunStore
) -> tuple[uuid.UUID, str, dict[str, str]]:
    """(project, solved run id, owner headers): Mark designs at 40 h a week."""
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    headers = auth(user)
    created = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "CRM"}, headers=headers)
    project = uuid.UUID(created.json()["id"])
    run_store.inputs[project] = PlanningInput(
        str(project),
        "CRM",
        date(2026, 10, 5),
        (Task("design", "1.1.1", "Design", 5, owner_id="mark"),),
        (Employee("mark", "Mark Chen"),),
    )
    run = client.post(f"/api/v1/projects/{project}/optimization/runs", headers=headers)
    assert run.status_code == 202, run.text
    run_id = run.json()["id"]
    run_store.runs[uuid.UUID(run_id)]["status"] = "optimal"
    return project, run_id, headers


def test_a_run_solved_before_an_input_change_is_not_applied(
    apply_client: TestClient, store: Store, run_store: RunStore, plans: FakePlans, auth: Auth
) -> None:
    project, run_id, headers = _solved_run(apply_client, store, auth, run_store)
    apply_url = f"/api/v1/projects/{project}/optimization/runs/{run_id}/apply"
    solved_on = run_store.inputs[project]
    # Mark now gives 16 h a week: the run booked him at 40.
    run_store.inputs[project] = replace(
        solved_on, employees=(Employee("mark", "Mark Chen", weekly_capacity_hours=16),)
    )

    response = apply_client.post(apply_url, headers=headers)
    assert (response.status_code, response.json()["detail"]) == (409, STALE)
    assert plans.applied == []

    run_store.inputs[project] = solved_on
    response = apply_client.post(apply_url, headers=headers)
    assert (response.status_code, response.json()["version"]) == (200, 1)
    assert plans.applied == [uuid.UUID(run_id)]


def test_a_run_without_an_inputs_hash_is_not_applied(
    apply_client: TestClient, store: Store, run_store: RunStore, plans: FakePlans, auth: Auth
) -> None:
    project, run_id, headers = _solved_run(apply_client, store, auth, run_store)
    run_store.runs[uuid.UUID(run_id)]["options"] = {}
    response = apply_client.post(
        f"/api/v1/projects/{project}/optimization/runs/{run_id}/apply", headers=headers
    )
    assert (response.status_code, response.json()["detail"]) == (409, STALE)
    assert plans.applied == []
