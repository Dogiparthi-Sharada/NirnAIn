"""Planning endpoints (constraints, capacity, skills, dependencies) with in-memory fakes."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient

from app.domain.common import OrgRole
from app.repositories.access import ProjectRef
from app.repositories.planning import PlanningRepository
from tests.support.connections import Conn, Rows
from tests.support.planning import CONSTRAINTS, PlanStore
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth


def test_constraints_defaults_and_put(plan_client: TestClient, store: Store, auth: Auth) -> None:
    user, org, pid = create_project(
        plan_client,
        store,
        auth,
        startDate="2026-11-02",
        targetEndDate="2027-01-29",
        budgetCents=500_000,
    )
    h = auth(user)
    url = f"/api/v1/projects/{pid}/constraints"
    body = plan_client.get(url, headers=h).json()
    assert body["budgetTotalCents"] == 500_000
    assert (body["startDate"], body["mustFinishBy"]) == ("2026-11-02", "2027-01-29")
    assert (body["budgetMode"], body["capacityMode"], body["workingDaysPerWeek"]) == (
        "hard",
        "soft",
        5,
    )
    assert body["milestones"] == []

    r = plan_client.put(url, json=CONSTRAINTS, headers=h)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["contingencyPct"] == 12.5
    assert saved["milestones"][0]["name"] == "Go-live"
    assert set(saved["milestones"][0]) == {"id", "name", "dueDate", "wbsNodeId", "mode"}
    assert plan_client.get(url, headers=h).json() == saved

    bad = {**CONSTRAINTS, "mustFinishBy": "2026-11-01"}
    assert plan_client.put(url, json=bad, headers=h).status_code == 422
    missing = {k: v for k, v in CONSTRAINTS.items() if k != "skillMode"}
    assert plan_client.put(url, json=missing, headers=h).status_code == 422
    foreign = {
        **CONSTRAINTS,
        "milestones": [
            {"name": "M", "dueDate": "2026-12-01", "mode": "hard", "wbsNodeId": str(uuid.uuid4())}
        ],
    }
    r = plan_client.put(url, json=foreign, headers=h)
    assert r.status_code == 422
    assert "wbsNodeId" in r.json()["detail"]

    viewer = store.add_user("Vic")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert plan_client.get(url, headers=auth(viewer)).status_code == 200
    assert plan_client.put(url, json=CONSTRAINTS, headers=auth(viewer)).status_code == 403
    outsider = store.add_user("Eve")
    assert plan_client.get(url, headers=auth(outsider)).status_code == 404


# --------------------------------------------------------------------------- repository SQL
async def test_repo_constraints_default_from_project() -> None:
    project = ProjectRef(uuid.uuid4(), uuid.uuid4(), "CRM", date(2026, 11, 2), None, 900)
    conn = Conn(Rows([]), Rows([]))
    cs = await PlanningRepository(conn).constraints(project)  # type: ignore[arg-type]
    assert (cs.budget_total_cents, cs.start_date, cs.must_finish_by) == (
        900,
        date(2026, 11, 2),
        None,
    )
    assert (cs.budget_mode, cs.deadline_mode, cs.capacity_mode, cs.skill_mode) == (
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
    user, _, pid = create_project(plan_client, store, auth)
    h = auth(user)
    url = f"/api/v1/projects/{pid}/constraints"
    body = plan_client.get(url, headers=h).json()
    assert (body["specialistAnswer"], body["deadlineAnswer"]) == (None, None)
    assert (body["allowContractors"], body["specialistRateConfirmed"]) == (True, False)

    answered = {**CONSTRAINTS, "specialistAnswer": "unknown", "deadlineAnswer": "soft"}
    saved = plan_client.put(url, json=answered, headers=h).json()
    assert (saved["specialistAnswer"], saved["deadlineAnswer"]) == ("unknown", "soft")
    assert (saved["deadlineMode"], saved["allowContractors"]) == ("soft", False)

    # An older client that doesn't send the answers keeps them.
    saved = plan_client.put(url, json=CONSTRAINTS, headers=h).json()
    assert (saved["specialistAnswer"], saved["deadlineAnswer"]) == ("unknown", "soft")
    answered = {**CONSTRAINTS, "specialistAnswer": "yes", "contractorRateCents": 15_000}
    saved = plan_client.put(url, json=answered, headers=h).json()
    assert (saved["allowContractors"], saved["contractorRateCents"]) == (True, 15_000)
    bad = {**CONSTRAINTS, "deadlineAnswer": "maybe"}
    assert plan_client.put(url, json=bad, headers=h).status_code == 422
