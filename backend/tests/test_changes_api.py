"""Step 7 endpoints with an in-memory change repository: the notes → answers → re-plan →
approve flow, edits / progress, reject, and permissions (viewer, outsider).

The fake mirrors RLS (project readers see, writers change) and BE1's apply function
(the applied run becomes the next version); C1's re-plan is simulated by linking a run.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import changes as changes_api
from app.changes.context import ChangeContext
from app.changes.llm import ChangeDetector
from app.core.security import CurrentPrincipal
from app.domain.changes import (
    ChangeImpact,
    ChangeRequest,
    ChangeSource,
    ChangeStatus,
    PlanOverrides,
)
from app.domain.common import OrgRole, SolverStatus
from app.repositories.access import ProjectRef
from app.repositories.change_plans import InForce, RunState
from app.repositories.changes import to_change
from tests.changes_fixtures import NOTES, SALESFORCE, SAP, project_context
from tests.conftest import Store

Auth = Callable[[uuid.UUID], dict[str, str]]


# --------------------------------------------------------------------------- API (fakes)
@dataclass
class ChangeStore:
    rows: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    runs: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    applied: list[uuid.UUID] = field(default_factory=list)
    written: list[PlanOverrides] = field(default_factory=list)


class FakeChanges:
    def __init__(self, store: Store, change_store: ChangeStore, user_id: uuid.UUID) -> None:
        self.store, self.change_store, self.user_id = store, change_store, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        project = self.store.projects.get(project_id)
        return self.store.role(project.org_id, self.user_id) if project else None

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        project = self.store.projects.get(project_id)
        if project is None or self._role(project_id) is None:
            return None
        return ProjectRef(
            project.id,
            project.org_id,
            project.name,
            project.start_date,
            project.target_end_date,
            project.budget_cents,
        )

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def in_force(self, project_id: uuid.UUID) -> InForce | None:
        applied = [
            (run_id, run)
            for run_id, run in self.change_store.runs.items()
            if run["project_id"] == project_id and run["applied"]
        ]
        if not applied:
            return None
        run_id, run = max(applied, key=lambda entry: entry[1]["version"] or 0)
        return InForce(run_id, run["version"], 34_860_000, date(2025, 9, 19))

    async def context(
        self, project: ProjectRef, in_force: InForce | None, today: date
    ) -> ChangeContext:
        context = project_context()
        return ChangeContext(
            budget_cents=context.budget_cents,
            deadline=context.deadline,
            start=context.start,
            tasks=context.tasks,
            people=context.people,
            edges=context.edges,
            plan_version=in_force.version if in_force else None,
            today=today,
        )

    async def create(
        self,
        project_id: uuid.UUID,
        source: ChangeSource,
        input_text: str | None,
        impact: ChangeImpact,
        base_run_id: uuid.UUID,
    ) -> uuid.UUID:
        change_id = uuid.uuid4()
        self.change_store.rows[change_id] = {
            "id": change_id,
            "project_id": project_id,
            "source": source,
            "input_text": input_text,
            "status": "ready",
            "impact": impact.model_dump(mode="json"),
            "answers": {},
            "base_run_id": base_run_id,
            "proposed_run_id": None,
            "error": None,
            "created_at": datetime.now(UTC),
            "decided_at": None,
        }
        return change_id

    def _view(self, row: dict[str, Any]) -> ChangeRequest:
        base = self.change_store.runs.get(row["base_run_id"]) or {}
        proposed_run = self.change_store.runs.get(row["proposed_run_id"]) or {}
        return to_change(
            {
                **row,
                "base_version": base.get("version"),
                "proposed_run_status": proposed_run.get("status"),
            }
        )

    async def get(self, change_id: uuid.UUID) -> ChangeRequest | None:
        row = self.change_store.rows.get(change_id)
        if row is None or self._role(row["project_id"]) is None:
            return None
        return self._view(row)

    async def list(self, project_id: uuid.UUID) -> list[ChangeRequest]:
        rows = [row for row in self.change_store.rows.values() if row["project_id"] == project_id]
        return [
            self._view(row) for row in sorted(rows, key=lambda row: row["created_at"], reverse=True)
        ]

    async def save_answers(
        self, change_id: uuid.UUID, answers: dict[str, str], *, reset_proposal: bool
    ) -> None:
        row = self.change_store.rows[change_id]
        row["answers"] = answers
        if reset_proposal:
            row.update(status="ready", proposed_run_id=None)

    async def decide(
        self, change_id: uuid.UUID, status: ChangeStatus, proposed_run_id: uuid.UUID | None
    ) -> None:
        row = self.change_store.rows[change_id]
        row.update(status=status.value, decided_at=datetime.now(UTC))
        if proposed_run_id:
            row["proposed_run_id"] = proposed_run_id

    async def run(self, run_id: uuid.UUID) -> RunState | None:
        run = self.change_store.runs.get(run_id)
        if run is None:
            return None
        return RunState(run_id, run["project_id"], run["status"], run["applied"], run["version"])

    async def apply_run(self, run_id: uuid.UUID) -> None:
        run = self.change_store.runs[run_id]
        run.update(
            applied=True,
            version=1 + max((entry["version"] or 0) for entry in self.change_store.runs.values()),
        )
        self.change_store.applied.append(run_id)

    async def write_overrides(self, project: ProjectRef, overrides: PlanOverrides) -> None:
        self.change_store.written.append(overrides)


@pytest.fixture
def change_store() -> ChangeStore:
    return ChangeStore()


@pytest.fixture
def change_client(app: FastAPI, store: Store, change_store: ChangeStore) -> Iterator[TestClient]:
    def repo(principal: CurrentPrincipal) -> FakeChanges:
        return FakeChanges(store, change_store, principal.user_id)

    app.dependency_overrides[changes_api.get_change_repo] = repo
    app.dependency_overrides[changes_api.get_change_detector] = lambda: ChangeDetector(None)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID, str]:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    response = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "EDP"}, headers=auth(user))
    return user, org, response.json()["id"]


def _run(
    change_store: ChangeStore, project_id: str, *, applied: bool, version: int | None = None
) -> uuid.UUID:
    run_id = uuid.uuid4()
    change_store.runs[run_id] = {
        "project_id": uuid.UUID(project_id),
        "status": SolverStatus.OPTIMAL,
        "applied": applied,
        "version": version,
    }
    return run_id


def test_change_flow_notes_to_approved_plan(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    user, _, project_id = _project(change_client, store, auth)
    headers = auth(user)
    url = f"/api/v1/projects/{project_id}/changes"

    response = change_client.post(url, json={"source": "notes", "text": NOTES}, headers=headers)
    assert response.status_code == 409  # nothing applied yet
    plan_v1_run = _run(change_store, project_id, applied=True, version=1)

    assert change_client.post(url, json={"source": "notes"}, headers=headers).status_code == 422
    response = change_client.post(url, json={"source": "notes", "text": NOTES}, headers=headers)
    assert response.status_code == 202, response.text
    change = response.json()
    change_id = change["id"]
    assert (change["status"], change["baseRunId"], change["baseVersion"]) == (
        "ready",
        str(plan_v1_run),
        1,
    )
    assert change["impact"]["model"] == "rules"
    assert change["overrides"] is None
    assert {question["id"] for question in change["impact"]["questions"]} == {"budget", "target:c4"}
    assert [listed["id"] for listed in change_client.get(url, headers=headers).json()] == [
        change_id
    ]

    # Approving before a re-plan exists is refused.
    assert (
        change_client.post(f"/api/v1/changes/{change_id}/approve", headers=headers).status_code
        == 409
    )
    bad = {"answers": {"budget": "123"}}
    assert (
        change_client.patch(f"/api/v1/changes/{change_id}", json=bad, headers=headers).status_code
        == 422
    )
    response = change_client.patch(
        f"/api/v1/changes/{change_id}",
        json={"answers": {"budget": "36500000", "target:c4": str(SAP)}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["overrides"] == {
        "budgetTotalCents": 36_500_000,
        "durationDays": {str(SALESFORCE): 0, str(SAP): 30},
    }

    # C1's re-plan run links itself to the change; it is proposed once it succeeds.
    plan_v2_run = _run(change_store, project_id, applied=False)
    change_store.runs[plan_v2_run]["status"] = SolverStatus.RUNNING
    change_store.rows[uuid.UUID(change_id)].update(status="replanning", proposed_run_id=plan_v2_run)
    assert (
        change_client.post(f"/api/v1/changes/{change_id}/approve", headers=headers).status_code
        == 409
    )
    change_store.runs[plan_v2_run]["status"] = SolverStatus.OPTIMAL
    assert (
        change_client.get(f"/api/v1/changes/{change_id}", headers=headers).json()["status"]
        == "proposed"
    )

    response = change_client.post(f"/api/v1/changes/{change_id}/approve", json={}, headers=headers)
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["decidedAt"] is not None) == (
        "approved",
        True,
    )
    assert change_store.applied == [plan_v2_run]
    assert change_store.runs[plan_v2_run]["version"] == 2
    assert change_store.written == [
        PlanOverrides(
            budget_total_cents=36_500_000, duration_days={str(SALESFORCE): 0, str(SAP): 30}
        )
    ]
    assert (
        change_client.post(f"/api/v1/changes/{change_id}/reject", headers=headers).status_code
        == 409
    )


def test_edits_reject_and_permissions(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    user, org, project_id = _project(change_client, store, auth)
    headers = auth(user)
    _run(change_store, project_id, applied=True, version=1)
    url = f"/api/v1/projects/{project_id}/changes"

    same = {"source": "edits", "edits": {"budgetTotalCents": 37_500_000}}
    assert change_client.post(url, json=same, headers=headers).status_code == 422
    unknown = {"source": "progress", "edits": {"progress": {str(uuid.uuid4()): 50}}}
    assert change_client.post(url, json=unknown, headers=headers).status_code == 422

    response = change_client.post(
        url, json={"source": "progress", "edits": {"progress": {str(SAP): 50}}}, headers=headers
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["impact"]["questions"] == []
    assert body["overrides"] == {"durationDays": {str(SAP): 10}}

    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    viewer_headers = auth(viewer)
    assert change_client.get(url, headers=viewer_headers).status_code == 200
    assert (
        change_client.post(
            f"/api/v1/changes/{body['id']}/reject", headers=viewer_headers
        ).status_code
        == 403
    )
    outsider = store.add_user("Olly Outsider")
    assert (
        change_client.get(f"/api/v1/changes/{body['id']}", headers=auth(outsider)).status_code
        == 404
    )

    response = change_client.post(f"/api/v1/changes/{body['id']}/reject", headers=headers)
    assert (response.status_code, response.json()["status"]) == (200, "rejected")
