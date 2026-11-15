"""Tests of the inputs sheet and of what a run is created from.

Purpose: Prove a run is refused (422) while the sheet lists missing inputs, the sheet shows rows,
blockers and the inputs hash, and a run freezes the body's overrides, remedy and baseline into its
snapshot and options.
How it fits: Exercises app/api/v1/optimization.py with the fakes of
tests/optimization_api/conftest.py; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.common import OrgRole
from app.optimization.model import Employee, PlanningInput, Task
from app.optimization.runtime import SOLVER_VERSION
from tests.support.optimization_runs import (
    RunStore,
    mark_ready,
    runs_url,
)
from tests.support.projects import create_project
from tests.support.runners import FakeRunner
from tests.support.store import Store
from tests.support.tokens import Auth


@pytest.mark.parametrize(
    ("tasks", "team", "missing", "detail"),
    [
        (
            False,
            False,
            ["workPackages", "team"],
            "Add work packages with durations on step 4 and add your team on step 5 before",
        ),
        (False, True, ["workPackages"], "Add work packages with durations on step 4 before"),
        (True, False, ["team"], "Add your team on step 5 before"),
    ],
)
def test_create_run_422_lists_missing_inputs(
    opt_client: TestClient,
    store: Store,
    run_store: RunStore,
    runner: FakeRunner,
    auth: Auth,
    tasks: bool,
    team: bool,
    missing: list[str],
    detail: str,
) -> None:
    """A run is refused with 422 listing every missing input, and nothing is submitted."""
    user, _, pid = create_project(opt_client, store, auth)
    run_store.inputs[uuid.UUID(pid)] = PlanningInput(
        pid,
        "CRM",
        date(2026, 10, 5),
        (Task("t1", "1.1", "A", 1),) if tasks else (),
        (Employee("e1", "A"),) if team else (),
    )
    response = opt_client.post(runs_url(pid), headers=auth(user))
    assert response.status_code == 422
    body = response.json()
    assert body["missing"] == missing
    assert detail in body["detail"]
    assert body["detail"].endswith("before running the optimizer.")
    assert runner.submitted == []
    assert run_store.runs == {}


def test_unconfirmed_ai_estimates_are_422(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    """Unconfirmed AI estimates block the run with "Confirm 2 AI estimates on step 4"."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    run_store.facts[uuid.UUID(pid)] = {"unconfirmed_estimates": 2}
    response = opt_client.post(runs_url(pid), headers=auth(user))
    assert response.status_code == 422
    body = response.json()
    assert body["missing"] == ["estimates"]
    assert body["detail"] == "Confirm 2 AI estimates on step 4 before running the optimizer."
    assert (runner.submitted, run_store.runs) == ([], {})


def test_inputs_sheet(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """The sheet lists every input with its source, blockers, missing keys and the inputs hash."""
    user, org, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    run_store.facts[uuid.UUID(pid)] = {"unconfirmed_estimates": 1, "team_from_project": True}
    response = opt_client.get(f"/api/v1/projects/{pid}/optimization/inputs", headers=auth(user))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"rows", "ready", "blocking", "missing", "hash", "appliedHash"}
    assert body["ready"] is False
    assert body["blocking"] == ["Confirm 1 AI estimate on step 4"]
    assert body["missing"] == ["estimates"]
    assert len(body["hash"]) == 64
    rows = {row["key"]: row for row in body["rows"]}
    assert rows["workPackages"] == {
        "key": "workPackages",
        "label": "Work packages",
        "value": "1 work package · 1 AI estimate not confirmed",
        "source": "needs_you",
        "from": "Step 4",
    }
    assert rows["team"]["value"] == "Mark Chen · capacity can flex"
    assert rows["team"]["source"] == "confirmed"

    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    url = f"/api/v1/projects/{pid}/optimization/inputs"
    assert opt_client.get(url, headers=auth(viewer)).status_code == 200
    outsider = store.add_user("Eve")
    assert opt_client.get(url, headers=auth(outsider)).status_code == 404


def test_run_body_is_applied_and_stored_as_options(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    """The body's overrides change the frozen input and are stored as the run's options."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    task_id, employee_id = uuid.uuid4(), uuid.uuid4()
    run_store.inputs[uuid.UUID(pid)] = PlanningInput(
        pid,
        "CRM",
        date(2026, 10, 5),
        (Task(str(task_id), "1.1.1", "Design", 5),),
        (Employee(str(employee_id), "Mark Chen"),),
    )
    baseline = opt_client.post(runs_url(pid), headers=auth(user)).json()["id"]
    run_store.runs[uuid.UUID(baseline)]["status"] = "optimal"
    run_store.owners[uuid.UUID(baseline)] = {str(task_id): str(employee_id)}
    change_id = uuid.uuid4()
    run_store.changes[change_id] = {"project_id": uuid.UUID(pid), "status": "ready"}

    body = {
        "remedy": "move_deadline",
        "overrides": {
            "budgetTotalCents": 5_000_000,
            "mustFinishBy": "2026-12-18",
            "durationDays": {str(task_id): 8},
            "hoursPerWeek": {str(employee_id): 20},
        },
        "changeRequestId": str(change_id),
        "baselineRunId": baseline,
    }
    response = opt_client.post(runs_url(pid), json=body, headers=auth(user))
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["options"] == {
        **body,
        "inputsHash": run["options"]["inputsHash"],
        "solverSettings": run["options"]["solverSettings"],
    }
    assert (run["version"], run["appliedAt"]) == (None, None)
    snapshot = run_store.runs[uuid.UUID(run["id"])]["snapshot"]
    constraints = snapshot["constraints"]
    assert constraints["budget_total_cents"] == 5_000_000
    assert constraints["must_finish_by"] == "2026-12-18"
    assert constraints["deadline_mode"] == "soft"  # the remedy, for this run
    assert snapshot["tasks"][0]["duration_days"] == 8
    assert snapshot["tasks"][0]["owner_id"] == str(employee_id)  # baseline owner
    assert snapshot["employees"][0]["weekly_capacity_hours"] == 20
    change = run_store.changes[change_id]
    assert (change["status"], change["proposed_run_id"]) == ("replanning", uuid.UUID(run["id"]))
    assert runner.submitted[-1] == uuid.UUID(run["id"])


def test_done_work_package_replans_from_the_plan_in_force(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """Finished work keeps its applied dates and a re-plan keeps the plan in force's completion."""
    # QA: Done -> Re-plan answered 422 because durationDays had to be at least 1.
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    task_id = uuid.uuid4()
    run_store.inputs[uuid.UUID(pid)] = PlanningInput(
        pid,
        "CRM",
        date(2026, 10, 5),
        (Task(str(task_id), "1.1.1", "Design", 5),),
        (Employee(str(uuid.uuid4()), "Mark Chen"),),
    )
    applied = opt_client.post(runs_url(pid), headers=auth(user)).json()["id"]
    schedule = {
        "wbsNodeId": str(task_id),
        "code": "1.1.1",
        "name": "Design",
        "deliverableCode": "1.1",
        "deliverableName": "Design",
        "start": "2026-10-05",
        "end": "2026-10-09",
    }
    summary = {
        "startDate": "2026-10-05",
        "completionDate": "2026-10-09",
        "deadline": None,
        "deadlineMode": "hard",
        "daysLate": 0,
        "costCents": 0,
        "budgetCents": None,
        "peakUtilization": None,
        "specialists": [],
        "softViolations": [],
    }
    run_store.runs[uuid.UUID(applied)].update(
        status="optimal", result={"schedule": [schedule], "summary": summary}
    )
    body = {
        "overrides": {"durationDays": {str(task_id): 0}},
        "baselineRunId": applied,
        "remedy": "overtime",
    }
    response = opt_client.post(runs_url(pid), json=body, headers=auth(user))
    assert response.status_code == 202, response.text
    snapshot = run_store.runs[uuid.UUID(response.json()["id"])]["snapshot"]
    (task,) = snapshot["tasks"]
    assert (task["done"], task["applied_start"], task["applied_end"]) == (
        True,
        "2026-10-05",
        "2026-10-09",
    )
    # The re-plan keeps the plan in force's date; the overtime option tries hard to.
    assert snapshot["target_date"] == "2026-10-09"
    assert snapshot["constraints"]["capacity_mode"] == "soft"
    assert snapshot["constraints"]["target_penalty_per_day"] > 0


def test_run_records_the_inputs_sheet_hash_and_solver_settings(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth, settings: Settings
) -> None:
    """A run records the sheet's inputs hash and the solver settings it is solved with."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    inputs = opt_client.get(f"/api/v1/projects/{pid}/optimization/inputs", headers=auth(user))
    sheet_hash = inputs.json()["hash"]

    plain = opt_client.post(runs_url(pid), headers=auth(user)).json()
    recorded = plain["options"].pop("solverSettings")
    assert plain["options"] == {"inputsHash": sheet_hash}
    # How the worker solves it (pack SOLVE-011), with this run's objective weights.
    assert (recorded["solver"], recorded["version"]) == ("OR-Tools CP-SAT", SOLVER_VERSION)
    assert (recorded["timeLimitS"], recorded["workers"], recorded["seed"]) == (
        settings.optimizer_time_limit_s,
        settings.optimizer_workers,
        0,
    )
    assert recorded["weights"]["makespanPerDayCents"] == 20_000

    run_store.runs[uuid.UUID(plain["id"])]["status"] = "optimal"
    # Overrides and remedies change the snapshot, not the saved inputs' fingerprint.
    what_if = {"remedy": "move_deadline", "overrides": {"budgetTotalCents": 1}}
    response = opt_client.post(runs_url(pid), json=what_if, headers=auth(user))
    assert response.status_code == 202, response.text
    options = response.json()["options"]
    assert options == {**what_if, "inputsHash": sheet_hash, "solverSettings": recorded}


def test_run_body_references_are_checked(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    """Unknown change requests and baselines are 404 and a decided change is 409."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    decided = uuid.uuid4()
    run_store.changes[decided] = {"project_id": uuid.UUID(pid), "status": "approved"}

    def post(body: dict[str, Any]) -> Any:
        """Post a run with the given body as the project's owner."""
        return opt_client.post(runs_url(pid), json=body, headers=auth(user))

    assert post({"changeRequestId": str(uuid.uuid4())}).status_code == 404
    assert post({"changeRequestId": str(decided)}).status_code == 409
    missing_baseline = post({"baselineRunId": str(uuid.uuid4())})
    assert missing_baseline.status_code == 404
    assert missing_baseline.json()["detail"] == "Baseline run not found"
    assert post({"remedy": "hire_everyone"}).status_code == 422
    assert post({"overrides": {"budget": 1}}).status_code == 422
    assert post({"overrides": {"durationDays": {str(uuid.uuid4()): -1}}}).status_code == 422
    assert (runner.submitted, run_store.runs) == ([], {})


def test_specialist_remedy_answers_the_specialist_question(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """The specialist remedy lifts the unanswered specialist blocker and allows a contractor."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    run_store.facts[uuid.UUID(pid)] = {"task_skill_gaps": ["SAP"]}
    blocked = opt_client.post(runs_url(pid), headers=auth(user))
    assert (blocked.status_code, blocked.json()["missing"]) == (422, ["specialist"])
    response = opt_client.post(runs_url(pid), json={"remedy": "specialist"}, headers=auth(user))
    assert response.status_code == 202, response.text
    snapshot = run_store.runs[uuid.UUID(response.json()["id"])]["snapshot"]
    assert snapshot["constraints"]["allow_contractors"] is True
    assert snapshot["constraints"]["contractor_rate_cents"] == 17_500


def test_combined_remedy_applies_every_relaxation(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    """The combined remedy allows a contractor and makes the deadline and budget soft."""
    user, _, pid = create_project(opt_client, store, auth)
    mark_ready(run_store, pid)
    run_store.facts[uuid.UUID(pid)] = {"task_skill_gaps": ["SAP"]}
    response = opt_client.post(runs_url(pid), json={"remedy": "combined"}, headers=auth(user))
    assert response.status_code == 202, response.text
    assert response.json()["options"]["remedy"] == "combined"
    constraints = run_store.runs[uuid.UUID(response.json()["id"])]["snapshot"]["constraints"]
    assert (constraints["allow_contractors"], constraints["contractor_rate_cents"]) == (
        True,
        17_500,
    )
    assert (constraints["deadline_mode"], constraints["budget_mode"]) == ("soft", "soft")
