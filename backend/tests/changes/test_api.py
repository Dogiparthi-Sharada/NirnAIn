"""Tests of the step 7 change endpoints on the in-memory change repository.

Purpose: Prove the notes → answers → re-plan → approve flow, structured edits and progress,
rejecting, permissions (viewer, outsider) and that an outdated re-plan never goes into force.
How it fits: The fake (tests/support/changes.py) mirrors RLS (project readers see, writers change)
and the apply function (the applied run becomes the next version); the re-plan is simulated by
linking a run; layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.llm import LLM, Usage
from app.api import deps
from app.api.v1 import changes as changes_api
from app.changes.llm import ChangeDetector, ProposedChanges
from app.domain.changes import PlanOverrides
from app.domain.common import OrgRole, SolverStatus
from tests.support.ai_usage import UsageLog
from tests.support.changes import (
    NOTES,
    SALESFORCE,
    SAP,
    ChangeStore,
    add_run,
)
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth


def test_change_flow_notes_to_approved_plan(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    """Notes become questions; answers become overrides; the solved re-plan becomes Plan v2.

    Also: 409 before a plan is in force or before a re-plan exists, 422 for bad bodies and
    answers, and the notes' new requirement joins the scope on approval.
    """
    user, _, project_id = create_project(change_client, store, auth, name="EDP")
    headers = auth(user)
    url = f"/api/v1/projects/{project_id}/changes"

    response = change_client.post(url, json={"source": "notes", "text": NOTES}, headers=headers)
    assert response.status_code == 409  # nothing applied yet
    plan_v1_run = add_run(change_store, project_id, applied=True, version=1)

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
    plan_v2_run = add_run(change_store, project_id, applied=False)
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
    # Plan v2 stands on the inputs the approval saved, so step 6 doesn't call it outdated.
    assert change_store.runs[plan_v2_run]["applied_inputs_hash"] == change_store.inputs_hash
    assert change_store.written == [
        PlanOverrides(
            budget_total_cents=36_500_000, duration_days={str(SALESFORCE): 0, str(SAP): 30}
        )
    ]
    # The notes' new requirement joins the scope (re-planning can't turn it into work).
    assert change_store.requirements == ["Workday has to be added as a data source."]
    assert (
        change_client.post(f"/api/v1/changes/{change_id}/reject", headers=headers).status_code
        == 409
    )


def test_edits_reject_and_permissions(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    """Edits that change nothing or name unknown tasks are 422; progress needs no questions.

    Viewers read but can't reject (403), outsiders get 404, and the owner rejects.
    """
    user, org, project_id = create_project(change_client, store, auth, name="EDP")
    headers = auth(user)
    add_run(change_store, project_id, applied=True, version=1)
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


def test_outdated_replan_is_not_approved(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    """R1: a re-plan solved on inputs edited since, or on another baseline, never goes into
    force; a fresh re-plan on the current inputs does."""
    user, _, project_id = create_project(change_client, store, auth, name="EDP")
    headers = auth(user)
    add_run(change_store, project_id, applied=True, version=1)
    progress = {"source": "progress", "edits": {"progress": {str(SAP): 100}}}
    response = change_client.post(
        f"/api/v1/projects/{project_id}/changes", json=progress, headers=headers
    )
    assert response.status_code == 202, response.text
    change_id = uuid.UUID(response.json()["id"])
    approve_url = f"/api/v1/changes/{change_id}/approve"

    stale = add_run(change_store, project_id, applied=False)
    change_store.rows[change_id].update(status="replanning", proposed_run_id=stale)
    outdated = "Plan v1 changed since this change was re-planned. Re-plan it again."
    # Someone edits a skill on step 5 after the re-plan was solved.
    change_store.inputs_hash = "inputs-after-a-skill-edit"
    response = change_client.post(approve_url, headers=headers)
    assert (response.status_code, response.json()["detail"]) == (409, outdated)

    change_store.inputs_hash = "saved-inputs"
    change_store.runs[stale]["baseline_run_id"] = uuid.uuid4()
    response = change_client.post(approve_url, headers=headers)
    assert (response.status_code, response.json()["detail"]) == (409, outdated)
    assert (change_store.applied, change_store.written) == ([], [])

    fresh = add_run(change_store, project_id, applied=False)
    change_store.rows[change_id]["proposed_run_id"] = fresh
    response = change_client.post(approve_url, headers=headers)
    assert (response.status_code, response.json()["status"]) == (200, "approved")
    assert change_store.applied == [fresh]


class _ClaudeReader(LLM):
    """A configured Claude stand-in: finds no change, reports 120 input and 30 output tokens."""

    def __init__(self) -> None:
        """Answer as the default AI model, without a client."""
        super().__init__(client=None, model="claude-sonnet-5-5")

    @property
    def configured(self) -> bool:
        """Report a configured key, so the detector asks Claude."""
        return True

    def parse(self, **fields: Any) -> Any:
        """Return no changes with the call's token use."""
        return ProposedChanges(changes=[]), Usage(120, 30)


def test_a_claude_reading_records_its_tokens(
    app: FastAPI, change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    """Reading notes with Claude records one usage row for the project, its workspace and the
    caller; a recording failure still records the change."""
    user, org, project_id = create_project(change_client, store, auth, name="EDP")
    add_run(change_store, project_id, applied=True, version=1)
    usage = UsageLog()
    app.dependency_overrides[changes_api.get_change_detector] = lambda: ChangeDetector(
        _ClaudeReader()
    )
    app.dependency_overrides[deps.get_ai_usage_connections] = lambda: usage
    url = f"/api/v1/projects/{project_id}/changes"
    body = {"source": "notes", "text": NOTES}
    assert change_client.post(url, json=body, headers=auth(user)).status_code == 202
    assert usage.summary() == [("change_detection", "claude-sonnet-5-5", 120, 30)]
    ((row),) = usage.rows
    assert (row["project_id"], row["org_id"], row["user_id"]) == (uuid.UUID(project_id), org, user)

    app.dependency_overrides[deps.get_ai_usage_connections] = lambda: UsageLog(fail=True)
    assert change_client.post(url, json=body, headers=auth(user)).status_code == 202
