"""Optimization endpoints with real JWT verification and in-memory fakes."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import Settings
from app.core.security import CurrentPrincipal
from app.domain.common import OrgRole, SolverStatus
from app.domain.optimization import OptimizationRun
from app.jobs.optimizer import RESTART_ERROR
from app.optimization.inputs import InputFacts, facts_from_input
from app.optimization.model import Employee, PlanningInput, Task
from app.optimization.runtime import SOLVER_VERSION
from app.repositories.optimization import (
    ActiveRun,
    ActiveRunExistsError,
    AppliedRun,
    ProjectContext,
)
from tests.conftest import Store, rls_violation

Auth = Callable[[uuid.UUID], dict[str, str]]


@dataclass
class RunStore:
    runs: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    inputs: dict[uuid.UUID, PlanningInput] = field(default_factory=dict)
    # Sheet facts only the database knows (answers, estimate confirmations), per project.
    facts: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    # Planned owners (WBS node id -> employee id) of earlier runs.
    owners: dict[uuid.UUID, dict[str, str]] = field(default_factory=dict)
    # Change requests: id -> {"project_id", "status", "proposed_run_id"}.
    changes: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    race: bool = False  # simulate a concurrent insert hitting the unique index


class FakeRuns:
    def __init__(self, store: Store, runs: RunStore, user_id: uuid.UUID) -> None:
        self.s, self.r, self.uid = store, runs, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project(self, project_id: uuid.UUID) -> ProjectContext | None:
        p = self.s.projects.get(project_id)
        if p is None or self._role(project_id) is None:
            return None
        return ProjectContext(p.id, p.org_id, p.name, p.start_date, p.target_end_date, None)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_s)
        for run in self.r.runs.values():
            if run["project_id"] == project_id and run["status"] in ("queued", "running"):
                return ActiveRun(run["id"], run["created_at"] < cutoff)
        return None

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        self.r.runs[run_id].update(status="failed", error=error)

    async def load_input(self, project: ProjectContext, today: date) -> PlanningInput:
        return self.r.inputs.get(
            project.id, PlanningInput(str(project.id), project.name, today, (), ())
        )

    async def input_facts(self, project: ProjectContext, inp: PlanningInput) -> InputFacts:
        return facts_from_input(inp, **self.r.facts.get(project.id, {}))

    async def baseline_owners(
        self, project_id: uuid.UUID, run_id: uuid.UUID
    ) -> dict[str, str] | None:
        run = self.r.runs.get(run_id)
        if run is None or run["project_id"] != project_id:
            return None
        return self.r.owners.get(run_id, {})

    async def applied_run(self, project_id: uuid.UUID) -> AppliedRun | None:
        applied = [
            run
            for run in self.r.runs.values()
            if run["project_id"] == project_id and run.get("applied_at") is not None
        ]
        if not applied:
            return None
        run = max(applied, key=lambda entry: entry["applied_at"])
        return AppliedRun(run["id"], (run.get("options") or {}).get("inputsHash"))

    async def change_request_status(
        self, project_id: uuid.UUID, change_request_id: uuid.UUID
    ) -> str | None:
        change = self.r.changes.get(change_request_id)
        return change["status"] if change and change["project_id"] == project_id else None

    async def mark_replanning(
        self, project_id: uuid.UUID, change_request_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        self.r.changes[change_request_id].update(status="replanning", proposed_run_id=run_id)

    async def create(
        self,
        project_id: uuid.UUID,
        snapshot: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        if not await self.can_write(project_id):
            raise rls_violation()
        if self.r.race:
            raise ActiveRunExistsError
        rid = uuid.uuid4()
        self.r.runs[rid] = {
            "id": rid,
            "project_id": project_id,
            "status": "queued",
            "solver": None,
            "solve_seconds": None,
            "error": None,
            "iis": None,
            "result": None,
            "created_at": datetime.now(UTC),
            "started_at": None,
            "finished_at": None,
            "options": options or {},
            "snapshot": snapshot,
        }
        return rid

    def _visible(self, run: dict[str, Any]) -> OptimizationRun | None:
        if self._role(run["project_id"]) is None:
            return None
        return OptimizationRun.model_validate({k: v for k, v in run.items() if k != "snapshot"})

    async def get(self, project_id: uuid.UUID, run_id: uuid.UUID) -> OptimizationRun | None:
        run = self.r.runs.get(run_id)
        return self._visible(run) if run and run["project_id"] == project_id else None

    async def latest(self, project_id: uuid.UUID) -> OptimizationRun | None:
        rows = [r for r in self.r.runs.values() if r["project_id"] == project_id]
        if not rows:
            return None
        return self._visible(max(rows, key=lambda r: r["created_at"]))


class FakeRunner:
    def __init__(self) -> None:
        self.submitted: list[uuid.UUID] = []

    async def submit(self, run_id: uuid.UUID) -> None:
        self.submitted.append(run_id)


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
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID, str]:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    r = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "CRM"}, headers=auth(user))
    return user, org, r.json()["id"]


def _ready(run_store: RunStore, project_id: str) -> None:
    pid = uuid.UUID(project_id)
    run_store.inputs[pid] = PlanningInput(
        project_id=project_id,
        project_name="CRM",
        start_date=date(2026, 10, 5),
        tasks=(Task("t1", "1.1.1", "Design", 5),),
        employees=(Employee("e1", "Mark Chen"),),
    )


def _url(project_id: str, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/optimization/runs{suffix}"


RUN_KEYS = {
    "id",
    "projectId",
    "status",
    "solver",
    "solveSeconds",
    "error",
    "iis",
    "result",
    "createdAt",
    "startedAt",
    "finishedAt",
    "version",
    "appliedAt",
    "options",
}


def test_create_run_is_202_and_submits(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 202, r.text
    body = r.json()
    assert set(body) == RUN_KEYS
    assert body["status"] == "queued"
    assert body["projectId"] == pid
    assert body["result"] is None
    assert r.headers["location"] == _url(pid, f"/{body['id']}")
    assert runner.submitted == [uuid.UUID(body["id"])]
    snapshot = run_store.runs[uuid.UUID(body["id"])]["snapshot"]
    assert snapshot["version"] == 1
    assert snapshot["tasks"][0]["code"] == "1.1.1"


def test_create_run_conflict_when_active(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    assert opt_client.post(_url(pid), headers=auth(user)).status_code == 202
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 409
    assert r.headers["content-type"] == "application/problem+json"
    assert r.json()["detail"] == "An optimization is already running for this project."
    assert len(runner.submitted) == 1


def test_create_run_conflict_on_race(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    run_store.race = True
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 409
    assert r.json()["detail"] == "An optimization is already running for this project."


def test_stale_active_run_is_failed_and_replaced(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    first = opt_client.post(_url(pid), headers=auth(user)).json()["id"]
    run_store.runs[uuid.UUID(first)]["created_at"] -= timedelta(hours=1)
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 202
    old = run_store.runs[uuid.UUID(first)]
    assert (old["status"], old["error"]) == ("failed", RESTART_ERROR)


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
    user, _, pid = _project(opt_client, store, auth)
    run_store.inputs[uuid.UUID(pid)] = PlanningInput(
        pid,
        "CRM",
        date(2026, 10, 5),
        (Task("t1", "1.1", "A", 1),) if tasks else (),
        (Employee("e1", "A"),) if team else (),
    )
    r = opt_client.post(_url(pid), headers=auth(user))
    assert r.status_code == 422
    body = r.json()
    assert body["missing"] == missing
    assert detail in body["detail"]
    assert body["detail"].endswith("before running the optimizer.")
    assert runner.submitted == []
    assert run_store.runs == {}


def test_create_run_permissions(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    owner, org, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    r = opt_client.post(_url(pid), headers=auth(viewer))
    assert r.status_code == 403
    assert r.headers["content-type"] == "application/problem+json"

    outsider = store.add_user("Eve Outsider")
    assert opt_client.post(_url(pid), headers=auth(outsider)).status_code == 404
    assert opt_client.post(_url(str(uuid.uuid4())), headers=auth(owner)).status_code == 404
    assert opt_client.post(_url(pid)).status_code == 401


def test_latest_and_get(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, org, pid = _project(opt_client, store, auth)
    r = opt_client.get(_url(pid, "/latest"), headers=auth(user))
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"

    _ready(run_store, pid)
    run_id = opt_client.post(_url(pid), headers=auth(user)).json()["id"]
    run = run_store.runs[uuid.UUID(run_id)]
    run.update(
        status=SolverStatus.INFEASIBLE,
        solver="OR-Tools CP-SAT 9.15.6755",
        solve_seconds=1.234,
        iis=[{"name": "Budget cap: $1", "explanation": "Too low."}],
        finished_at=datetime.now(UTC),
    )
    r = opt_client.get(_url(pid, "/latest"), headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == RUN_KEYS
    assert body["id"] == run_id
    assert body["status"] == "infeasible"
    assert body["solveSeconds"] == 1.234
    assert body["iis"] == [{"name": "Budget cap: $1", "explanation": "Too low."}]

    assert opt_client.get(_url(pid, f"/{run_id}"), headers=auth(user)).status_code == 200
    assert opt_client.get(_url(pid, f"/{uuid.uuid4()}"), headers=auth(user)).status_code == 404
    assert opt_client.get(_url(pid, "/not-a-uuid"), headers=auth(user)).status_code == 422

    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert opt_client.get(_url(pid, "/latest"), headers=auth(viewer)).status_code == 200
    outsider = store.add_user("Eve")
    assert opt_client.get(_url(pid, "/latest"), headers=auth(outsider)).status_code == 404
    assert opt_client.get(_url(pid, f"/{run_id}"), headers=auth(outsider)).status_code == 404


def test_finished_run_result_is_camel_case(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    run_id = opt_client.post(_url(pid), headers=auth(user)).json()["id"]
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
    body = opt_client.get(_url(pid, f"/{run_id}"), headers=auth(user)).json()
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


def test_unconfirmed_ai_estimates_are_422(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    run_store.facts[uuid.UUID(pid)] = {"unconfirmed_estimates": 2}
    response = opt_client.post(_url(pid), headers=auth(user))
    assert response.status_code == 422
    body = response.json()
    assert body["missing"] == ["estimates"]
    assert body["detail"] == "Confirm 2 AI estimates on step 4 before running the optimizer."
    assert (runner.submitted, run_store.runs) == ([], {})


def test_inputs_sheet(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, org, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
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
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    task_id, employee_id = uuid.uuid4(), uuid.uuid4()
    run_store.inputs[uuid.UUID(pid)] = PlanningInput(
        pid,
        "CRM",
        date(2026, 10, 5),
        (Task(str(task_id), "1.1.1", "Design", 5),),
        (Employee(str(employee_id), "Mark Chen"),),
    )
    baseline = opt_client.post(_url(pid), headers=auth(user)).json()["id"]
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
    response = opt_client.post(_url(pid), json=body, headers=auth(user))
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
    # QA: Done -> Re-plan answered 422 because durationDays had to be at least 1.
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    task_id = uuid.uuid4()
    run_store.inputs[uuid.UUID(pid)] = PlanningInput(
        pid,
        "CRM",
        date(2026, 10, 5),
        (Task(str(task_id), "1.1.1", "Design", 5),),
        (Employee(str(uuid.uuid4()), "Mark Chen"),),
    )
    applied = opt_client.post(_url(pid), headers=auth(user)).json()["id"]
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
    response = opt_client.post(_url(pid), json=body, headers=auth(user))
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
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    inputs = opt_client.get(f"/api/v1/projects/{pid}/optimization/inputs", headers=auth(user))
    sheet_hash = inputs.json()["hash"]

    plain = opt_client.post(_url(pid), headers=auth(user)).json()
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
    response = opt_client.post(_url(pid), json=what_if, headers=auth(user))
    assert response.status_code == 202, response.text
    options = response.json()["options"]
    assert options == {**what_if, "inputsHash": sheet_hash, "solverSettings": recorded}


def test_run_body_references_are_checked(
    opt_client: TestClient, store: Store, run_store: RunStore, runner: FakeRunner, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    decided = uuid.uuid4()
    run_store.changes[decided] = {"project_id": uuid.UUID(pid), "status": "approved"}

    def post(body: dict[str, Any]) -> Any:
        return opt_client.post(_url(pid), json=body, headers=auth(user))

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
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    run_store.facts[uuid.UUID(pid)] = {"task_skill_gaps": ["SAP"]}
    blocked = opt_client.post(_url(pid), headers=auth(user))
    assert (blocked.status_code, blocked.json()["missing"]) == (422, ["specialist"])
    response = opt_client.post(_url(pid), json={"remedy": "specialist"}, headers=auth(user))
    assert response.status_code == 202, response.text
    snapshot = run_store.runs[uuid.UUID(response.json()["id"])]["snapshot"]
    assert snapshot["constraints"]["allow_contractors"] is True
    assert snapshot["constraints"]["contractor_rate_cents"] == 17_500


def test_combined_remedy_applies_every_relaxation(
    opt_client: TestClient, store: Store, run_store: RunStore, auth: Auth
) -> None:
    user, _, pid = _project(opt_client, store, auth)
    _ready(run_store, pid)
    run_store.facts[uuid.UUID(pid)] = {"task_skill_gaps": ["SAP"]}
    response = opt_client.post(_url(pid), json={"remedy": "combined"}, headers=auth(user))
    assert response.status_code == 202, response.text
    assert response.json()["options"]["remedy"] == "combined"
    constraints = run_store.runs[uuid.UUID(response.json()["id"])]["snapshot"]["constraints"]
    assert (constraints["allow_contractors"], constraints["contractor_rate_cents"]) == (
        True,
        17_500,
    )
    assert (constraints["deadline_mode"], constraints["budget_mode"]) == ("soft", "soft")


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
