"""Starting an optimizer run only from complete, reviewed inputs.

What: a work package without a usable duration blocks the run and is named on the inputs
sheet (WBS-003, WBS-004) instead of silently missing from an "optimal" plan; a run starts
only from the inputs the PM reviewed (CON-019), and never twice at once (NFR-005).
Why: QA's pack 2 found plans that left out mandatory work with nothing said.
How it fits: the API runs against the in-memory `FakeRuns` of test_optimization_api; the
repository query is compiled against the recording connection of test_repositories.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.optimization.model import Task
from app.repositories.optimization import OptimizationRepository
from app.repositories.work_packages import packages_without_duration
from tests.support.connections import Conn, Rows
from tests.support.store import Store
from tests.test_optimization_api import (
    Auth,
    FakeRunner,
    FakeRuns,
    RunStore,
    _project,
    _ready,
    _url,
)

MAPPING = "1.2.1 W06 Data mapping has no duration: enter one on step 4"


@pytest.fixture
def run_store() -> RunStore:
    return RunStore()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def opt_client(
    app: FastAPI, store: Store, run_store: RunStore, runner: FakeRunner
) -> Iterator[TestClient]:
    def runs(principal: CurrentPrincipal) -> FakeRuns:
        return FakeRuns(store, run_store, principal.user_id)

    app.dependency_overrides[deps.get_optimization_repo] = runs
    app.dependency_overrides[deps.get_optimization_runner] = lambda: runner
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _inputs_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/optimization/inputs"


def test_a_package_without_duration_blocks_the_sheet_and_the_run(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, project_id = _project(opt_client, store, auth)
    _ready(run_store, project_id)
    run_store.without_duration[uuid.UUID(project_id)] = ["1.2.1 W06 Data mapping"]

    sheet = opt_client.get(_inputs_url(project_id), headers=auth(user)).json()
    assert (sheet["ready"], sheet["blocking"], sheet["missing"]) == (
        False,
        [MAPPING],
        ["durations"],
    )
    work_row = next(row for row in sheet["rows"] if row["key"] == "workPackages")
    assert work_row["source"] == "needs_you"
    assert work_row["value"].endswith("· 1 without a duration")

    refused = opt_client.post(_url(project_id), headers=auth(user))
    assert refused.status_code == 422
    assert refused.json()["detail"] == f"{MAPPING} before running the optimizer."
    assert refused.json()["missing"] == ["durations"]
    assert (runner.submitted, run_store.runs) == ([], {})


def test_many_packages_without_duration_are_named_once(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, project_id = _project(opt_client, store, auth)
    _ready(run_store, project_id)
    labels = [f"1.1.{number} Package {number}" for number in range(1, 8)]
    run_store.without_duration[uuid.UUID(project_id)] = labels

    sheet = opt_client.get(_inputs_url(project_id), headers=auth(user)).json()
    assert sheet["blocking"] == [
        "1.1.1 Package 1, 1.1.2 Package 2, 1.1.3 Package 3, 1.1.4 Package 4, "
        "1.1.5 Package 5 and 2 more have no duration: enter them on step 4"
    ]


def test_a_run_starts_only_from_the_inputs_the_pm_reviewed(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    """CON-019: approving a sheet whose inputs changed since is refused with 409."""
    user, _, project_id = _project(opt_client, store, auth)
    _ready(run_store, project_id)
    reviewed = opt_client.get(_inputs_url(project_id), headers=auth(user)).json()["hash"]
    saved = run_store.inputs[uuid.UUID(project_id)]
    run_store.inputs[uuid.UUID(project_id)] = dataclasses.replace(
        saved, tasks=(Task("t1", "1.1.1", "Design", 9),)
    )

    stale = opt_client.post(_url(project_id), json={"reviewedHash": reviewed}, headers=auth(user))
    assert (stale.status_code, stale.json()["detail"]) == (
        409,
        "The inputs changed since you reviewed them. Review again.",
    )
    assert (runner.submitted, run_store.runs) == ([], {})

    current = opt_client.get(_inputs_url(project_id), headers=auth(user)).json()["hash"]
    started = opt_client.post(_url(project_id), json={"reviewedHash": current}, headers=auth(user))
    assert started.status_code == 202
    assert started.json()["options"]["reviewedHash"] == current
    malformed = opt_client.post(_url(project_id), json={"reviewedHash": "abc"}, headers=auth(user))
    assert malformed.status_code == 422


def test_a_second_start_while_one_is_starting_is_refused(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    """NFR-005: a double-click's second request is refused, not queued as a second run."""
    user, _, project_id = _project(opt_client, store, auth)
    _ready(run_store, project_id)
    run_store.starting.add(uuid.UUID(project_id))

    second = opt_client.post(_url(project_id), headers=auth(user))

    assert (second.status_code, second.json()["detail"]) == (
        409,
        "An optimization is already running for this project.",
    )
    assert (runner.submitted, run_store.runs) == ([], {})


async def test_run_creation_claims_a_transaction_lock_on_the_project() -> None:
    project_id = uuid.uuid4()
    connection = Conn(Rows([{"claimed": False}]))

    claimed = await OptimizationRepository(connection).lock_run_creation(project_id)  # type: ignore[arg-type]

    assert claimed is False
    assert "pg_try_advisory_xact_lock(hashtextextended(" in connection.sql[0]
    assert f"optimization_runs:{project_id}" in connection.params[0].values()


def _node(
    node_id: uuid.UUID, parent: uuid.UUID | None, level: int, **values: Any
) -> dict[str, Any]:
    """A `wbs_nodes_with_code` row."""
    return {"id": node_id, "parent_id": parent, "level": level, "duration_days": None} | values


async def test_packages_without_a_usable_duration_are_listed() -> None:
    root, deliverable, empty_deliverable, blank, done, never, planned = (
        uuid.uuid4() for _ in range(7)
    )
    connection = Conn(
        Rows(
            [
                _node(root, None, 1, code="1", name="Root"),
                _node(deliverable, root, 2, code="1.1", name="Build"),
                _node(blank, deliverable, 3, code="1.1.1", name="Mapping"),
                _node(done, deliverable, 3, code="1.1.2", name="Done", duration_days=0),
                _node(never, deliverable, 3, code="1.1.3", name="Go-live", duration_days=0),
                _node(planned, deliverable, 3, code="1.1.4", name="Planned", duration_days=5),
                _node(empty_deliverable, root, 2, code="1.2", name="Reports"),
            ]
        ),
        Rows([{"node_id": str(done), "first_day": "2026-10-05", "last_day": "2026-10-09"}]),
    )

    labels = await packages_without_duration(connection, uuid.uuid4())  # type: ignore[arg-type]

    assert labels == ["1.1.1 Mapping", "1.1.3 Go-live", "1.2 Reports"]
    assert "wbs_nodes_with_code" in connection.sql[0]
    assert "applied_at IS NOT NULL" in connection.sql[1]
