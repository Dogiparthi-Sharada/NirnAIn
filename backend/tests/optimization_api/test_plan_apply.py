"""Applying a run on step 6 (round 3, R1: never apply an outdated plan).

Purpose: QA's CHANGE-005 applied an old result after a person's skills were cut; now a run whose
recorded `inputsHash` no longer matches the saved inputs (or has none) is refused with 409, and a
current one is applied.
How it fits: Exercises app/api/v1/plan.py with the fakes of tests/optimization_api/conftest.py plus
a plan repository that records what was applied; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.optimization.model import Employee, PlanningInput, Task
from tests.support.optimization_runs import RunStore
from tests.support.store import Store
from tests.support.tokens import Auth

STALE = "The inputs changed since this plan was computed. Run the optimizer again before applying."


class FakePlans:
    """`public.apply_optimization_run`: the run becomes the next plan version."""

    def __init__(self, run_store: RunStore) -> None:
        """Record applies into the run store."""
        self.run_store = run_store
        self.applied: list[uuid.UUID] = []

    async def apply(self, run_id: uuid.UUID) -> None:
        """Mark the run applied as Plan v1 and remember it."""
        self.run_store.runs[run_id].update(version=1, applied_at=datetime.now(UTC))
        self.applied.append(run_id)


@pytest.fixture
def plans(run_store: RunStore) -> FakePlans:
    """Provide a plan repository that records applied runs."""
    return FakePlans(run_store)


@pytest.fixture
def apply_client(app: FastAPI, opt_client: TestClient, plans: FakePlans) -> TestClient:
    """Provide the optimization client with a plan repository that records what was applied."""
    app.dependency_overrides[deps.get_plan_repo] = lambda: plans
    return opt_client


def _solved_run(
    client: TestClient, store: Store, auth: Auth, run_store: RunStore
) -> tuple[uuid.UUID, str, dict[str, str]]:
    """Return a project with a solved run (its inputs hash recorded) and the owner's headers."""
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
    """A run solved before an input change is refused with 409; once current, it is applied."""
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
    """A run without a recorded inputs hash can't prove it is current and is refused with 409."""
    project, run_id, headers = _solved_run(apply_client, store, auth, run_store)
    run_store.runs[uuid.UUID(run_id)]["options"] = {}
    response = apply_client.post(
        f"/api/v1/projects/{project}/optimization/runs/{run_id}/apply", headers=headers
    )
    assert (response.status_code, response.json()["detail"]) == (409, STALE)
    assert plans.applied == []
