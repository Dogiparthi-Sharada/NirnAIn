"""Tests of mid-project team changes on step 7: bringing in a specialist, taking someone off.

Purpose: QA found these product-owner scenarios impossible after Plan v1. Prove direct edits
(`addMembers` / `removeMembers`) and QA's notes reach the plan, the 422 rules, releasing reserved
newcomers on reject or "Don't bring them in", and the comparison's team reasons.
How it fits: Runs the real step 7 endpoints on the in-memory repository of tests/support/changes.py;
layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.changes.compare import build_comparison
from app.changes.context import NodeRow, PlanRow, RunMeta
from app.changes.derive import build_impact, from_edits
from app.domain.changes import ChangeEdits, NewMember
from app.domain.common import SolverStatus
from tests.support.changes import (
    ETL,
    LEO,
    MARK,
    QA_NOTES,
    SALESFORCE,
    SARA,
    ChangeStore,
    add_run,
    project_context,
    qa_context,
)
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth

DANA: dict[str, Any] = {
    "fullName": "Dana Lee",
    "roleTitle": "Security Specialist",
    "hoursPerWeek": 24,
    "skills": ["Security & Compliance Review"],
    "workerType": "contractor",
    "hourlyRateCents": 17_500,
}


def _propose(change_store: ChangeStore, project_id: str, change_id: str) -> None:
    """C1's re-plan run linked to the change and solved."""
    run_id = add_run(change_store, project_id, applied=False)
    change_store.rows[uuid.UUID(change_id)].update(status="replanning", proposed_run_id=run_id)


def test_bring_in_a_specialist_then_remove_someone(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    """Dana is reserved inactive, joins on approval; removing Mark puts his packages in play."""
    user, _, project_id = create_project(change_client, store, auth, name="EDP")
    headers = auth(user)
    add_run(change_store, project_id, applied=True, version=1)
    url = f"/api/v1/projects/{project_id}/changes"

    body = {"source": "edits", "edits": {"addMembers": [DANA]}}
    response = change_client.post(url, json=body, headers=headers)
    assert response.status_code == 202, response.text
    change = response.json()
    [added] = change["impact"]["changes"]
    reserved = added["target"]
    assert (added["kind"], added["action"], added["targetLabel"], added["to"]) == (
        "team",
        "add",
        "Dana Lee",
        24,
    )
    assert change["impact"]["questions"] == []
    assert {"id": reserved, "name": "Dana Lee"} in change["impact"]["affects"]["people"]
    # Reserved as an inactive candidate until the change is approved.
    assert change_store.employees[uuid.UUID(reserved)] == {"name": "Dana Lee", "active": False}
    assert change["overrides"] == {"addMembers": [{**DANA, "employeeId": reserved}]}

    _propose(change_store, project_id, change["id"])
    approved = change_client.post(f"/api/v1/changes/{change['id']}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    assert change_store.employees[uuid.UUID(reserved)]["active"] is True
    [written] = change_store.written
    assert [member.full_name for member in written.add_members or []] == ["Dana Lee"]

    body = {"source": "edits", "edits": {"removeMembers": [str(MARK)]}}
    response = change_client.post(url, json=body, headers=headers)
    assert response.status_code == 202, response.text
    removal = response.json()
    [removed] = removal["impact"]["changes"]
    assert (removed["kind"], removed["action"], removed["target"], removed["to"]) == (
        "team",
        "remove",
        str(MARK),
        0,
    )
    # Mark's packages (and what follows them) are what the re-plan has to reassign.
    affected = {task["code"] for task in removal["impact"]["affects"]["tasks"]}
    assert affected == {"1.1.1", "1.2.2"}
    assert removal["overrides"] == {"removeMembers": [str(MARK)]}


@pytest.mark.parametrize(
    ("edits", "message"),
    [
        ({"addMembers": [{**DANA, "fullName": "mark  chen"}]}, "mark chen is already on the team"),
        ({"addMembers": [DANA, {**DANA, "fullName": "dana lee"}]}, "dana lee is listed twice"),
        ({"removeMembers": [str(uuid.uuid4())]}, "isn't on the team"),
        ({"removeMembers": [str(MARK), str(SARA)]}, "at least one person"),
        ({"addMembers": [{**DANA, "hoursPerWeek": 0}]}, None),
        ({"addMembers": [{**DANA, "hoursPerWeek": 81}]}, None),
        (
            {"addMembers": [{**DANA, "workerType": "employee"}]},
            "Employee rates come from the company record.",
        ),
        (
            {"addMembers": [{**DANA, "hourlyRateCents": None}]},
            "Enter the contractor's hourly rate for Dana Lee.",
        ),
        ({"addMembers": [{**DANA, "fullName": f"Person {index}"} for index in range(11)]}, None),
    ],
)
def test_team_edits_that_cannot_apply_are_refused(
    change_client: TestClient,
    store: Store,
    change_store: ChangeStore,
    auth: Auth,
    edits: dict[str, Any],
    message: str | None,
) -> None:
    """Duplicate, unknown or out-of-range team edits are 422 and reserve nobody."""
    user, _, project_id = create_project(change_client, store, auth, name="EDP")
    add_run(change_store, project_id, applied=True, version=1)
    response = change_client.post(
        f"/api/v1/projects/{project_id}/changes",
        json={"source": "edits", "edits": edits},
        headers=auth(user),
    )
    assert response.status_code == 422
    if message:
        assert message in response.json()["detail"]
    assert change_store.employees == {}  # nothing reserved for a refused change


def test_contractor_rate_defaults_to_the_projects_and_employees_have_none(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    """A contractor without a rate takes the project's; an employee gets none."""
    user, _, project_id = create_project(change_client, store, auth, name="EDP")
    add_run(change_store, project_id, applied=True, version=1)
    change_store.context = replace(project_context(), contractor_rate_cents=15_000)
    contractor = {**DANA, "hourlyRateCents": None}
    employee = {**DANA, "fullName": "Ana Souza", "workerType": "employee", "hourlyRateCents": None}
    response = change_client.post(
        f"/api/v1/projects/{project_id}/changes",
        json={"source": "edits", "edits": {"addMembers": [contractor, employee]}},
        headers=auth(user),
    )
    assert response.status_code == 202, response.text
    added = response.json()["overrides"]["addMembers"]
    assert [(member["workerType"], member["hourlyRateCents"]) for member in added] == [
        ("contractor", 15_000),
        ("employee", None),
    ]


def test_replacing_the_whole_team_is_allowed() -> None:
    """Removing everyone is allowed when someone joins in the same change."""
    edits = ChangeEdits(add_members=[NewMember.model_validate(DANA)], remove_members=[MARK, SARA])
    found = from_edits(edits, project_context())
    assert [(change.action, change.target_label) for change in found] == [
        ("add", "Dana Lee"),
        ("remove", "Mark Chen"),
        ("remove", "Sara Kim"),
    ]


def test_notes_newcomer_released_when_rejected_or_declined(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    """A newcomer reserved from notes is released when the change is rejected or declined."""
    user, _, project_id = create_project(change_client, store, auth, name="EDP")
    headers = auth(user)
    change_store.context = qa_context()
    add_run(change_store, project_id, applied=True, version=2)
    url = f"/api/v1/projects/{project_id}/changes"

    first = change_client.post(url, json={"source": "notes", "text": QA_NOTES}, headers=headers)
    assert first.status_code == 202, first.text
    kinds = {change["kind"] for change in first.json()["impact"]["changes"]}
    assert {"team", "capacity", "budget", "deadline"} <= kinds
    rejected = change_client.post(f"/api/v1/changes/{first.json()['id']}/reject", headers=headers)
    assert rejected.status_code == 200
    assert change_store.employees == {}  # the candidate is gone

    second = change_client.post(url, json={"source": "notes", "text": QA_NOTES}, headers=headers)
    change_id = second.json()["id"]
    answers = {
        "team:c1": "keep",
        "rate:c1": "keep",
        "capacity:c2": "new",
        "budget": "keep",
        "deadline": "keep",
    }
    answered = change_client.patch(
        f"/api/v1/changes/{change_id}", json={"answers": answers}, headers=headers
    )
    assert answered.json()["overrides"] == {"hoursPerWeek": {str(LEO): 16.0}}
    _propose(change_store, project_id, change_id)
    approved = change_client.post(f"/api/v1/changes/{change_id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    assert change_store.employees == {}  # declined: released on approval too


def test_existing_employee_is_reused_and_never_released(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    """An active employee with the same name is reused and survives a rejection."""
    user, _, project_id = create_project(change_client, store, auth, name="EDP")
    headers = auth(user)
    add_run(change_store, project_id, applied=True, version=1)
    dana_id = uuid.uuid4()
    change_store.employees[dana_id] = {"name": "Dana Lee", "active": True}
    response = change_client.post(
        f"/api/v1/projects/{project_id}/changes",
        json={"source": "edits", "edits": {"addMembers": [{**DANA, "fullName": "DANA LEE"}]}},
        headers=headers,
    )
    assert response.json()["impact"]["changes"][0]["target"] == str(dana_id)
    change_client.post(f"/api/v1/changes/{response.json()['id']}/reject", headers=headers)
    assert dana_id in change_store.employees


def test_comparison_names_who_left_and_who_joined() -> None:
    """The comparison's reasons name who leaves and who joins the team."""
    context = project_context()
    dana = uuid.uuid4()
    found = from_edits(
        ChangeEdits(add_members=[NewMember.model_validate(DANA)], remove_members=[MARK]), context
    )
    found[0] = found[0].model_copy(update={"target": dana})
    impact = build_impact(found, "edits", context, "rules")
    snapshot = {"start_date": "2025-01-13", "constraints": {"working_days_per_week": 5}}
    base = RunMeta(uuid.uuid4(), uuid.uuid4(), SolverStatus.OPTIMAL, 1, 1, None, snapshot)
    proposed = RunMeta(uuid.uuid4(), base.project_id, SolverStatus.OPTIMAL, None, 2, None, snapshot)
    nodes = [
        NodeRow(SALESFORCE, None, 2, "1.1", "Salesforce connector"),
        NodeRow(ETL, None, 2, "1.2", "ETL pipeline"),
    ]
    before = [PlanRow(SALESFORCE, MARK, None, 0, 2), PlanRow(ETL, SARA, None, 3, 5)]
    after = [PlanRow(SALESFORCE, SARA, None, 0, 2), PlanRow(ETL, dana, None, 3, 5)]
    names = {MARK: "Mark Chen", SARA: "Sara Kim", dana: "Dana Lee"}
    comparison = build_comparison(base, before, proposed, after, nodes, names, impact)
    rows = {row.code: (row.after.owner if row.after else None, row.why) for row in comparison.rows}
    assert rows == {
        "1.1": ("Sara Kim", "Mark Chen leaves the team"),
        "1.2": ("Dana Lee", "Dana Lee joins the team"),
    }
