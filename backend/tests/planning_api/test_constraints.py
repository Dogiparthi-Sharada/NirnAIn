"""Tests of the project constraints endpoint and the answers to the plan-changing questions.

Purpose: Prove constraints default from the project until saved, are validated and saved with their
milestones, follow the deadline and specialist answers, and are read from the database.
How it fits: Exercises app/api/v1/planning.py with the fake planning repository and
app/repositories/planning.py with a fake connection; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient

from app.domain.common import OrgRole
from app.domain.planning import ConstraintSetInput
from app.repositories.access import ProjectRef
from app.repositories.planning import PlanningRepository
from tests.support.connections import Conn, Rows
from tests.support.planning import CONSTRAINTS, PlanStore
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth


def test_constraints_defaults_and_put(plan_client: TestClient, store: Store, auth: Auth) -> None:
    """Constraints default from the project; PUT validates and saves them; viewers only read."""
    user, org, project_id = create_project(
        plan_client,
        store,
        auth,
        startDate="2026-11-02",
        targetEndDate="2027-01-29",
        budgetCents=500_000,
    )
    headers = auth(user)
    url = f"/api/v1/projects/{project_id}/constraints"
    body = plan_client.get(url, headers=headers).json()
    assert body["budgetTotalCents"] == 500_000
    assert (body["startDate"], body["mustFinishBy"]) == ("2026-11-02", "2027-01-29")
    assert (body["budgetMode"], body["capacityMode"], body["workingDaysPerWeek"]) == (
        "hard",
        "soft",
        5,
    )
    assert body["milestones"] == []

    response = plan_client.put(url, json=CONSTRAINTS, headers=headers)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["contingencyPct"] == 12.5
    assert saved["milestones"][0]["name"] == "Go-live"
    assert set(saved["milestones"][0]) == {"id", "name", "dueDate", "wbsNodeId", "mode"}
    assert plan_client.get(url, headers=headers).json() == saved

    bad = {**CONSTRAINTS, "mustFinishBy": "2026-11-01"}
    assert plan_client.put(url, json=bad, headers=headers).status_code == 422
    missing = {
        field_name: value for field_name, value in CONSTRAINTS.items() if field_name != "skillMode"
    }
    assert plan_client.put(url, json=missing, headers=headers).status_code == 422
    foreign = {
        **CONSTRAINTS,
        "milestones": [
            {"name": "M", "dueDate": "2026-12-01", "mode": "hard", "wbsNodeId": str(uuid.uuid4())}
        ],
    }
    response = plan_client.put(url, json=foreign, headers=headers)
    assert response.status_code == 422
    assert "wbsNodeId" in response.json()["detail"]

    viewer = store.add_user("Vic")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert plan_client.get(url, headers=auth(viewer)).status_code == 200
    assert plan_client.put(url, json=CONSTRAINTS, headers=auth(viewer)).status_code == 403
    outsider = store.add_user("Eve")
    assert plan_client.get(url, headers=auth(outsider)).status_code == 404


def test_put_constraints_refuses_dates_crossing_the_projects(
    plan_client: TestClient, store: Store, auth: Auth
) -> None:
    """Step-5 dates become the project's; one left empty keeps the project's, so a new start after
    the project's end (or a deadline before its start) is refused rather than half-saved."""
    user, _, project_id = create_project(
        plan_client, store, auth, startDate="2026-11-02", targetEndDate="2027-01-29"
    )
    url = f"/api/v1/projects/{project_id}/constraints"
    late_start = {**CONSTRAINTS, "startDate": "2027-02-01", "mustFinishBy": None}
    response = plan_client.put(url, json=late_start, headers=auth(user))
    assert response.status_code == 422
    assert "set mustFinishBy as well" in response.json()["detail"]
    early_end = {**CONSTRAINTS, "startDate": None, "mustFinishBy": "2026-10-30"}
    response = plan_client.put(url, json=early_end, headers=auth(user))
    assert response.status_code == 422
    assert "set startDate as well" in response.json()["detail"]
    moved = {**CONSTRAINTS, "startDate": "2027-02-01", "mustFinishBy": "2027-06-30"}
    assert plan_client.put(url, json=moved, headers=auth(user)).status_code == 200


# --------------------------------------------------------------------------- repository SQL
async def test_repo_save_constraints_syncs_the_project_dates() -> None:
    """Saving step 5 writes its start and must-finish-by on the project in the same transaction;
    a cleared date keeps the project's."""
    project_id = uuid.uuid4()
    conn = Conn()
    body = ConstraintSetInput.model_validate({**CONSTRAINTS, "milestones": []})
    await PlanningRepository(conn).save_constraints(project_id, body)  # type: ignore[arg-type]
    (update,) = [index for index, sql in enumerate(conn.sql) if "UPDATE public.projects" in sql]
    assert (conn.params[update]["start_date"], conn.params[update]["target_end_date"]) == (
        date(2026, 11, 16),
        date(2026, 12, 31),
    )
    assert conn.params[update]["id_1"] == project_id

    cleared = Conn()
    body = ConstraintSetInput.model_validate(
        {**CONSTRAINTS, "milestones": [], "mustFinishBy": None}
    )
    await PlanningRepository(cleared).save_constraints(project_id, body)  # type: ignore[arg-type]
    (sql,) = [sql for sql in cleared.sql if "UPDATE public.projects" in sql]
    assert "start_date=" in sql
    assert "target_end_date" not in sql
    neither = Conn()
    body = ConstraintSetInput.model_validate(
        {**CONSTRAINTS, "milestones": [], "startDate": None, "mustFinishBy": None}
    )
    await PlanningRepository(neither).save_constraints(project_id, body)  # type: ignore[arg-type]
    assert not [sql for sql in neither.sql if "UPDATE public.projects" in sql]


async def test_repo_constraints_default_from_project() -> None:
    """Without a saved set the repository returns the project's budget, dates and defaults."""
    project = ProjectRef(uuid.uuid4(), uuid.uuid4(), "CRM", date(2026, 11, 2), None, 900)
    conn = Conn(Rows([]), Rows([]))
    constraint_set = await PlanningRepository(conn).constraints(project)  # type: ignore[arg-type]
    assert (
        constraint_set.budget_total_cents,
        constraint_set.start_date,
        constraint_set.must_finish_by,
    ) == (
        900,
        date(2026, 11, 2),
        None,
    )
    assert (
        constraint_set.budget_mode,
        constraint_set.deadline_mode,
        constraint_set.capacity_mode,
        constraint_set.skill_mode,
    ) == (
        "hard",
        "hard",
        "soft",
        "hard",
    )
    assert "FROM public.constraint_sets" in conn.sql[0]
    assert "FROM public.milestones" in conn.sql[1]


def test_constraint_answers(
    plan_client: TestClient, store: Store, plan_store: PlanStore, auth: Auth
) -> None:
    """Saving the answers sets the deadline mode and whether contractors are allowed."""
    user, _, project_id = create_project(plan_client, store, auth)
    headers = auth(user)
    url = f"/api/v1/projects/{project_id}/constraints"
    body = plan_client.get(url, headers=headers).json()
    assert (body["specialistAnswer"], body["deadlineAnswer"]) == (None, None)
    assert (body["allowContractors"], body["specialistRateConfirmed"]) == (True, False)

    answered = {**CONSTRAINTS, "specialistAnswer": "unknown", "deadlineAnswer": "soft"}
    saved = plan_client.put(url, json=answered, headers=headers).json()
    assert (saved["specialistAnswer"], saved["deadlineAnswer"]) == ("unknown", "soft")
    assert (saved["deadlineMode"], saved["allowContractors"]) == ("soft", False)

    # An older client that doesn't send the answers keeps them.
    saved = plan_client.put(url, json=CONSTRAINTS, headers=headers).json()
    assert (saved["specialistAnswer"], saved["deadlineAnswer"]) == ("unknown", "soft")
    answered = {**CONSTRAINTS, "specialistAnswer": "yes", "contractorRateCents": 15_000}
    saved = plan_client.put(url, json=answered, headers=headers).json()
    assert (saved["allowContractors"], saved["contractorRateCents"]) == (True, 15_000)
    bad = {**CONSTRAINTS, "deadlineAnswer": "maybe"}
    assert plan_client.put(url, json=bad, headers=headers).status_code == 422
