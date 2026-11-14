"""Requirements are inputs too: once a plan version is applied, intake runs and scope items
are frozen like the team, constraints and WBS (409 pointing to step 7).

What: POST intake runs, POST and PATCH scope items before and after Plan v1.
Why: QA could still add scope items after Plan v1 (201), silently changing the inputs of an
applied plan; changes after Plan v1 go through step 7. How it fits: reuses the in-memory
intake fakes of tests/test_intake_api.py with the plan lock dependency overridden.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import intake as intake_api
from app.core.security import CurrentPrincipal
from tests.conftest import Store
from tests.test_intake_api import FakeIntaker, FakeIntakes, FakePlanLock, IntakeStore

Auth = Callable[[uuid.UUID], dict[str, str]]
LOCKED = "Plan v1 is in force; record a change on step 7"


@pytest.fixture
def intake_data() -> IntakeStore:
    return IntakeStore()


@pytest.fixture
def locked_client(app: FastAPI, store: Store, intake_data: IntakeStore) -> Iterator[TestClient]:
    def intakes(principal: CurrentPrincipal) -> FakeIntakes:
        return FakeIntakes(store, intake_data, principal.user_id)

    app.dependency_overrides[deps.get_intake_repo] = intakes
    app.dependency_overrides[deps.get_intake_runner] = FakeIntaker
    app.dependency_overrides[intake_api.get_plan_lock] = lambda: FakePlanLock(intake_data)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_requirements_freeze_once_plan_v1_is_applied(
    locked_client: TestClient, store: Store, intake_data: IntakeStore, auth: Auth
) -> None:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    headers = auth(user)
    project_id = locked_client.post(
        f"/api/v1/orgs/{org}/projects", json={"name": "EDP"}, headers=headers
    ).json()["id"]
    runs_url = f"/api/v1/projects/{project_id}/intake/runs"
    items_url = f"/api/v1/projects/{project_id}/scope-items"
    item = {"category": "requirement", "text": "Workday as a data source"}

    assert locked_client.post(items_url, json=item, headers=headers).status_code == 201
    assert locked_client.post(runs_url, json={"text": "notes"}, headers=headers).status_code == 202

    intake_data.applied_version = 1
    for response in (
        locked_client.post(items_url, json=item, headers=headers),
        locked_client.post(runs_url, json={"text": "more notes"}, headers=headers),
        locked_client.patch(
            f"/api/v1/scope-items/{next(iter(intake_data.items))}",
            json={"status": "removed"},
            headers=headers,
        ),
    ):
        assert response.status_code == 409
        assert (response.json()["detail"], response.json()["version"]) == (LOCKED, 1)
    assert len(intake_data.items) == 1
    assert len(intake_data.runs) == 1
    # Reading stays open.
    assert locked_client.get(runs_url, headers=headers).status_code == 200
