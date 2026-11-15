"""Tests of capacity, skill coverage and the resource pool, and the planning-window arithmetic.

Purpose: Prove capacity averages other projects' bookings over the planning window, coverage tells
which required skills the team has, and the window and averages are computed as specified.
How it fits: Exercises app/api/v1/planning.py with the fake repository, the pure functions in
app/domain/planning.py and app/repositories/planning.py with a fake connection; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

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
    """Capacity is averaged over the planning window: the project's dates, then the constraints'."""
    user, _, project_id = create_project(
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
    response = plan_client.get(f"/api/v1/projects/{project_id}/capacity", headers=auth(user))
    assert response.status_code == 200, response.text
    assert response.json() == [
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
    assert plan_store.weeks[-1] == [date(2026, 11, 2) + timedelta(weeks=week) for week in range(4)]

    plan_client.put(
        f"/api/v1/projects/{project_id}/constraints", json=CONSTRAINTS, headers=auth(user)
    )
    plan_client.get(f"/api/v1/projects/{project_id}/capacity", headers=auth(user))
    assert plan_store.weeks[-1][0] == date(2026, 11, 16)
    assert plan_store.weeks[-1][-1] == date(2026, 12, 28)


def test_skills_coverage_and_employees(
    plan_client: TestClient, store: Store, plan_store: PlanStore, auth: Auth
) -> None:
    """Coverage tells which required skills someone holds; the pool lists active employees."""
    user, org, project_id = create_project(plan_client, store, auth)
    sap, python, excel = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    plan_store.required = {sap: ("SAP", 2), python: ("python", 3)}
    plan_store.held = {python: ("python", 4), excel: ("Excel", 1)}
    response = plan_client.get(f"/api/v1/projects/{project_id}/skills-coverage", headers=auth(user))
    assert response.status_code == 200
    assert [
        (coverage["name"], coverage["required"], coverage["available"])
        for coverage in response.json()
    ] == [
        ("python", True, True),
        ("SAP", True, False),
        ("Excel", False, True),
    ]
    assert set(response.json()[0]) == {"skillId", "name", "required", "available"}

    response = plan_client.get(f"/api/v1/orgs/{org}/employees", headers=auth(user))
    assert (response.status_code, response.json()) == (200, [])
    outsider = store.add_user("Eve")
    assert (
        plan_client.get(f"/api/v1/orgs/{org}/employees", headers=auth(outsider)).status_code == 404
    )
    assert (
        plan_client.get(
            f"/api/v1/projects/{project_id}/skills-coverage", headers=auth(outsider)
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
    """The window is the Mondays from the start (or today) to the end, else 12 weeks."""
    weeks = planning_weeks(start, end, date(2026, 11, 13))
    assert (weeks[0], len(weeks)) == (first, count)
    assert all(week.weekday() == 0 for week in weeks)


@pytest.mark.parametrize(
    ("total", "weeks", "expected"),
    [(30, 7, 4.5), (0, 4, 0.0), (10, 4, 2.5), (11, 4, 3.0), (40, 0, 0.0), (99, 3, 33.0)],
)
def test_average_weekly_hours(total: float, weeks: int, expected: float) -> None:
    """Average weekly hours count empty weeks as 0 and round to the nearest half hour."""
    assert average_weekly_hours(total, weeks) == expected


def test_skill_coverage_levels() -> None:
    """A skill counts only at the required level; skills nobody needs are listed as optional."""
    crm, sql, okta = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    out = skill_coverage(
        {crm: ("Design", 4), sql: ("Python", 3)},
        {crm: ("Design", 3), sql: ("Python", 3), okta: ("Agile", 1)},
    )
    assert [(coverage.name, coverage.required, coverage.available) for coverage in out] == [
        ("Design", True, False),
        ("Python", True, True),
        ("Agile", False, True),
    ]
    assert skill_coverage({}, {}) == []


async def test_repo_capacity_averages_other_projects() -> None:
    """The repository averages only other projects' bookings inside the window."""
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
