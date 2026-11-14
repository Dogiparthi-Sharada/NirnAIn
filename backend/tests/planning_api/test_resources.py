"""Moved test support."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.domain.planning import (
    PoolEmployee,
    average_weekly_hours,
    planning_weeks,
    skill_coverage,
)
from app.repositories.access import ProjectRef
from app.repositories.planning import PlanningRepository
from tests.support.connections import Conn, Rows
from tests.support.planning import CONSTRAINTS, PlanStore
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth


def test_capacity_uses_planning_window(
    plan_client: TestClient, store: Store, plan_store: PlanStore, auth: Auth
) -> None:
    user, _, pid = create_project(
        plan_client, store, auth, startDate="2026-11-02", targetEndDate="2026-11-29"
    )
    plan_store.team = [
        PoolEmployee(
            employee_id=uuid.uuid4(),
            full_name="Ada",
            role_title="Engineer",
            weekly_capacity_hours=40,
            hourly_rate_cents=10_000,
            is_contractor=False,
            skills=[{"skill_id": uuid.uuid4(), "name": "Python", "proficiency": 4}],  # type: ignore[list-item]
        )
    ]
    r = plan_client.get(f"/api/v1/projects/{pid}/capacity", headers=auth(user))
    assert r.status_code == 200, r.text
    assert r.json() == [
        {
            "employeeId": str(plan_store.team[0].employee_id),
            "fullName": "Ada",
            "roleTitle": "Engineer",
            "weeklyCapacityHours": 40.0,
            "allocatedHours": 7.5,
            "hourlyRateCents": 10_000,
            "isContractor": False,
            "skills": [
                {
                    "skillId": str(plan_store.team[0].skills[0].skill_id),
                    "name": "Python",
                    "proficiency": 4,
                }
            ],
        }
    ]
    assert plan_store.weeks[-1] == [date(2026, 11, 2) + timedelta(weeks=i) for i in range(4)]

    plan_client.put(f"/api/v1/projects/{pid}/constraints", json=CONSTRAINTS, headers=auth(user))
    plan_client.get(f"/api/v1/projects/{pid}/capacity", headers=auth(user))
    assert plan_store.weeks[-1][0] == date(2026, 11, 16)
    assert plan_store.weeks[-1][-1] == date(2026, 12, 28)


def test_skills_coverage_and_employees(
    plan_client: TestClient, store: Store, plan_store: PlanStore, auth: Auth
) -> None:
    user, org, pid = create_project(plan_client, store, auth)
    sap, python, excel = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    plan_store.required = {sap: ("SAP", 2), python: ("python", 3)}
    plan_store.held = {python: ("python", 4), excel: ("Excel", 1)}
    r = plan_client.get(f"/api/v1/projects/{pid}/skills-coverage", headers=auth(user))
    assert r.status_code == 200
    assert [(c["name"], c["required"], c["available"]) for c in r.json()] == [
        ("python", True, True),
        ("SAP", True, False),
        ("Excel", False, True),
    ]
    assert set(r.json()[0]) == {"skillId", "name", "required", "available"}

    r = plan_client.get(f"/api/v1/orgs/{org}/employees", headers=auth(user))
    assert (r.status_code, r.json()) == (200, [])
    outsider = store.add_user("Eve")
    assert (
        plan_client.get(f"/api/v1/orgs/{org}/employees", headers=auth(outsider)).status_code == 404
    )
    assert (
        plan_client.get(
            f"/api/v1/projects/{pid}/skills-coverage", headers=auth(outsider)
        ).status_code
        == 404
    )


# --------------------------------------------------------------------------- computations
@pytest.mark.parametrize(
    ("start", "end", "first", "count"),
    [
        (date(2026, 11, 18), date(2026, 12, 4), date(2026, 11, 16), 3),
        (None, None, date(2026, 11, 9), 12),
        (date(2026, 11, 16), None, date(2026, 11, 16), 12),
        (date(2026, 11, 16), date(2026, 11, 1), date(2026, 11, 16), 12),
        (None, date(2026, 11, 20), date(2026, 11, 9), 2),
    ],
)
def test_planning_weeks(start: date | None, end: date | None, first: date, count: int) -> None:
    weeks = planning_weeks(start, end, date(2026, 11, 13))
    assert (weeks[0], len(weeks)) == (first, count)
    assert all(w.weekday() == 0 for w in weeks)


@pytest.mark.parametrize(
    ("total", "weeks", "expected"),
    [(30, 7, 4.5), (0, 4, 0.0), (10, 4, 2.5), (11, 4, 3.0), (40, 0, 0.0), (99, 3, 33.0)],
)
def test_average_weekly_hours(total: float, weeks: int, expected: float) -> None:
    assert average_weekly_hours(total, weeks) == expected


def test_skill_coverage_levels() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    out = skill_coverage(
        {a: ("Design", 4), b: ("Python", 3)}, {a: ("Design", 3), b: ("Python", 3), c: ("Agile", 1)}
    )
    assert [(x.name, x.required, x.available) for x in out] == [
        ("Design", True, False),
        ("Python", True, True),
        ("Agile", False, True),
    ]
    assert skill_coverage({}, {}) == []


async def test_repo_capacity_averages_other_projects() -> None:
    project = ProjectRef(uuid.uuid4(), uuid.uuid4(), "CRM", None, None, None)
    emp = uuid.uuid4()
    row = {
        "employee_id": emp,
        "full_name": "Ada",
        "role_title": None,
        "weekly_capacity_hours": 40,
        "hourly_rate_cents": None,
        "is_contractor": False,
        "total": 30,
    }
    conn = Conn(Rows([row]), Rows([]))
    weeks = planning_weeks(date(2026, 11, 16), date(2026, 12, 31), date(2026, 11, 13))
    [entry] = await PlanningRepository(conn).capacity(project, weeks)  # type: ignore[arg-type]
    assert (entry.employee_id, entry.allocated_hours, entry.skills) == (emp, 4.5, [])
    assert "allocations.project_id != " in conn.sql[0]
    assert conn.params[0]["week_start_1"] == date(2026, 11, 16)
    assert conn.params[0]["week_start_2"] == date(2026, 12, 28)
