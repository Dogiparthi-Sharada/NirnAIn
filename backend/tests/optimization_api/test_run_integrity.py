"""Starting an optimizer run only from complete, reviewed inputs.

Purpose: QA's pack 2 found plans that left out mandatory work silently: a package without a usable
duration now blocks the run and is named on the sheet (WBS-003, WBS-004), a run starts only from the
inputs the PM reviewed (CON-019), and never twice at once (NFR-005).
How it fits: Exercises app/api/v1/optimization.py with the fakes of
tests/optimization_api/conftest.py, and the repository queries on a recording connection;
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.optimization.model import Task
from app.repositories.optimization import OptimizationRepository
from app.repositories.work_packages import packages_without_duration
from tests.support.connections import Conn, Rows
from tests.support.optimization_runs import (
    RunStore,
    mark_ready,
    runs_url,
)
from tests.support.projects import create_project
from tests.support.runners import FakeRunner
from tests.support.store import Store
from tests.support.tokens import Auth

MAPPING = "1.2.1 W06 Data mapping has no duration: enter one on step 4"


def _inputs_url(project_id: str) -> str:
    """Return the project's inputs sheet URL."""
    return f"/api/v1/projects/{project_id}/optimization/inputs"


def test_a_package_without_duration_blocks_the_sheet_and_the_run(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    """A package without a duration blocks the sheet and the run, and is named on the sheet."""
    user, _, project_id = create_project(opt_client, store, auth)
    mark_ready(run_store, project_id)
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

    refused = opt_client.post(runs_url(project_id), headers=auth(user))
    assert refused.status_code == 422
    assert refused.json()["detail"] == f"{MAPPING} before running the optimizer."
    assert refused.json()["missing"] == ["durations"]
    assert (runner.submitted, run_store.runs) == ([], {})


def test_many_packages_without_duration_are_named_once(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """Many packages without a duration are named once, the first few and "N more"."""
    user, _, project_id = create_project(opt_client, store, auth)
    mark_ready(run_store, project_id)
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
    user, _, project_id = create_project(opt_client, store, auth)
    mark_ready(run_store, project_id)
    reviewed = opt_client.get(_inputs_url(project_id), headers=auth(user)).json()["hash"]
    saved = run_store.inputs[uuid.UUID(project_id)]
    run_store.inputs[uuid.UUID(project_id)] = dataclasses.replace(
        saved, tasks=(Task("t1", "1.1.1", "Design", 9),)
    )

    stale = opt_client.post(
        runs_url(project_id), json={"reviewedHash": reviewed}, headers=auth(user)
    )
    assert (stale.status_code, stale.json()["detail"]) == (
        409,
        "The inputs changed since you reviewed them. Review again.",
    )
    assert (runner.submitted, run_store.runs) == ([], {})

    current = opt_client.get(_inputs_url(project_id), headers=auth(user)).json()["hash"]
    started = opt_client.post(
        runs_url(project_id), json={"reviewedHash": current}, headers=auth(user)
    )
    assert started.status_code == 202
    assert started.json()["options"]["reviewedHash"] == current
    malformed = opt_client.post(
        runs_url(project_id), json={"reviewedHash": "abc"}, headers=auth(user)
    )
    assert malformed.status_code == 422


def test_a_second_start_while_one_is_starting_is_refused(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    """NFR-005: a double-click's second request is refused, not queued as a second run."""
    user, _, project_id = create_project(opt_client, store, auth)
    mark_ready(run_store, project_id)
    run_store.starting.add(uuid.UUID(project_id))

    second = opt_client.post(runs_url(project_id), headers=auth(user))

    assert (second.status_code, second.json()["detail"]) == (
        409,
        "An optimization is already running for this project.",
    )
    assert (runner.submitted, run_store.runs) == ([], {})


async def test_run_creation_claims_a_transaction_lock_on_the_project() -> None:
    """Run creation takes a transaction-scoped advisory lock keyed by the project."""
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
    """Packages with no duration, or 0 days and never planned, are listed; finished work is not."""
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
