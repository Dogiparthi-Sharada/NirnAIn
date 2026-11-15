"""Tests of the project team and skills endpoints.

Purpose: Prove people are added once (reusing organization employees), with hours, worker type and
rate rules; skills are listed with holders, renamed onto existing ones and removed; viewers only
read and outsiders see nothing.
How it fits: Exercises app/api/v1/team.py with the fake repository of tests/support/team.py,
`clean_skill_names` and `TeamRepository.members` on a recording connection; layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import OrgRole
from app.domain.planning import planning_weeks
from app.domain.team import clean_skill_names
from app.repositories.access import ProjectRef
from app.repositories.team import TeamRepository
from tests.support.connections import Conn, Rows
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.team import FakeTeam, TeamStore
from tests.support.tokens import Auth


@pytest.fixture
def team_store() -> TeamStore:
    """Provide empty fake team data."""
    return TeamStore()


@pytest.fixture
def team_client(app: FastAPI, store: Store, team_store: TeamStore) -> Iterator[TestClient]:
    """Provide a test client whose team repository is the fake."""

    def repo(principal: CurrentPrincipal) -> FakeTeam:
        """Return the fake team repository acting as the caller."""
        return FakeTeam(store, team_store, principal.user_id)

    app.dependency_overrides[deps.get_team_repo] = repo
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# The planning window of the team tests' project.
DATES = {"startDate": "2026-11-16", "targetEndDate": "2027-01-29"}
SARA = {"fullName": "Sara Kim", "roleTitle": "Lead Architect", "hoursPerWeek": 40, "skills": []}


def test_team_crud(
    team_client: TestClient, store: Store, team_store: TeamStore, auth: Auth
) -> None:
    """People are added once, reusing employees, changed and removed; viewers only read."""
    user, org, project_id = create_project(team_client, store, auth, **DATES)
    headers = auth(user)
    url = f"/api/v1/projects/{project_id}/team"
    assert team_client.get(url, headers=headers).json() == []

    existing = uuid.uuid4()
    team_store.employees[existing] = {"org_id": org, "full_name": "Mark Chen"}
    team_store.booked[existing] = 12.5
    body = {
        "fullName": "  mark   chen ",
        "roleTitle": "Data Engineer",
        "hoursPerWeek": 30,
        "skills": ["Spark", "spark"],
    }
    response = team_client.post(url, json=body, headers=headers)
    assert response.status_code == 201, response.text
    mark = response.json()
    assert mark == {
        "employeeId": str(existing),
        "fullName": "Mark Chen",
        "roleTitle": "Data Engineer",
        "hoursPerWeek": 30,
        "workerType": "employee",
        "hourlyRateCents": None,
        "bookedHours": 12.5,
        "skills": ["Spark"],
    }
    assert team_store.weeks[-1][0] == date(2026, 11, 16)

    response = team_client.post(url, json=SARA, headers=headers)
    assert response.status_code == 201
    sara = response.json()["employeeId"]
    assert team_store.employees[uuid.UUID(sara)]["weekly_capacity_hours"] == 40
    response = team_client.post(url, json={**SARA, "fullName": "SARA KIM"}, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "SARA KIM is already on the team"
    for bad in (
        {**SARA, "hoursPerWeek": 0},
        {**SARA, "hoursPerWeek": 81},
        {**SARA, "fullName": " "},
    ):
        assert team_client.post(url, json=bad, headers=headers).status_code == 422

    member = f"{url}/{sara}"
    response = team_client.patch(
        member, json={"hoursPerWeek": 20, "skills": ["Data Modeling"]}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert (response.json()["hoursPerWeek"], response.json()["skills"]) == (20, ["Data Modeling"])
    response = team_client.patch(member, json={"fullName": "Mark Chen"}, headers=headers)
    assert response.status_code == 409
    response = team_client.patch(member, json={"fullName": "Sara Kimura"}, headers=headers)
    assert response.json()["fullName"] == "Sara Kimura"
    assert team_client.patch(member, json={}, headers=headers).status_code == 422
    assert (
        team_client.patch(member, json={"hoursPerWeek": None}, headers=headers).status_code == 422
    )
    other = f"{url}/{uuid.uuid4()}"
    assert team_client.patch(other, json={"hoursPerWeek": 5}, headers=headers).status_code == 404

    viewer = store.add_user("Vic")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert team_client.get(url, headers=auth(viewer)).status_code == 200
    assert team_client.delete(member, headers=auth(viewer)).status_code == 403

    assert team_client.delete(member, headers=headers).status_code == 204
    assert team_client.delete(member, headers=headers).status_code == 404
    assert [listed["fullName"] for listed in team_client.get(url, headers=headers).json()] == [
        "Mark Chen"
    ]
    outsider = store.add_user("Eve")
    assert team_client.get(url, headers=auth(outsider)).status_code == 404


def test_team_worker_types_and_rates(
    team_client: TestClient, store: Store, team_store: TeamStore, auth: Auth
) -> None:
    """Employees take their record's rate; contractors need one, or the project's confirmed one."""
    user, org, project_id = create_project(team_client, store, auth, **DATES)
    headers = auth(user)
    url = f"/api/v1/projects/{project_id}/team"

    def post(body: dict[str, Any]) -> Any:
        """Add a team member with `body`."""
        return team_client.post(url, json=body, headers=headers)

    # Employees are costed at their company record's rate: none is typed here.
    refused = post({**SARA, "hourlyRateCents": 9_500})
    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "Employee rates come from the company record.",
    )
    sara = post(SARA).json()
    assert (sara["workerType"], sara["hourlyRateCents"]) == ("employee", None)

    # A contractor needs a rate, unless the project has a confirmed contractor rate.
    dana = {**SARA, "fullName": "Dana Lee", "workerType": "contractor"}
    refused = post(dana)
    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "Enter the contractor's hourly rate.",
    )
    for bad in (-1, 100_000_001, 12.5, "lots"):
        assert post({**dana, "hourlyRateCents": bad}).status_code == 422, bad
    team_store.contractor_rates[uuid.UUID(project_id)] = 18_500
    added = post(dana)
    assert added.status_code == 201, added.text
    assert (added.json()["workerType"], added.json()["hourlyRateCents"]) == ("contractor", 18_500)
    dana_id = added.json()["employeeId"]
    assert team_store.employees[uuid.UUID(dana_id)]["is_contractor"] is True

    def patch(employee_id: str, body: dict[str, Any]) -> Any:
        """Change a team member with `body`."""
        return team_client.patch(f"{url}/{employee_id}", json=body, headers=headers)

    assert patch(dana_id, {"hourlyRateCents": 12_000}).json()["hourlyRateCents"] == 12_000
    assert patch(dana_id, {"hoursPerWeek": 20}).json()["hourlyRateCents"] == 12_000
    # As an employee the rate on record becomes their company rate; it can't be typed.
    hired = patch(dana_id, {"workerType": "employee"}).json()
    assert (hired["workerType"], hired["hourlyRateCents"]) == ("employee", 12_000)
    assert patch(dana_id, {"hourlyRateCents": 9_000}).status_code == 422
    # Sara becomes a contractor at the project's confirmed rate; her rate can't be cleared.
    converted = patch(sara["employeeId"], {"workerType": "contractor"}).json()
    assert (converted["workerType"], converted["hourlyRateCents"]) == ("contractor", 18_500)
    del team_store.contractor_rates[uuid.UUID(project_id)]
    cleared = patch(sara["employeeId"], {"hourlyRateCents": None})
    assert (cleared.status_code, cleared.json()["detail"]) == (
        422,
        "Enter the contractor's hourly rate.",
    )

    # Reusing an organization employee keeps the rate on their record.
    known = uuid.uuid4()
    team_store.employees[known] = {"org_id": org, "full_name": "Leo", "hourly_rate_cents": 8_000}
    leo = post({**SARA, "fullName": "Leo"}).json()
    assert (leo["workerType"], leo["hourlyRateCents"]) == ("employee", 8_000)


def test_skills_crud(
    team_client: TestClient, store: Store, team_store: TeamStore, auth: Auth
) -> None:
    """Skills list with holders, are added once, renamed (onto existing ones) and removed."""
    user, org, project_id = create_project(team_client, store, auth, **DATES)
    headers = auth(user)
    url = f"/api/v1/projects/{project_id}/skills"
    sap = uuid.uuid4()
    team_store.skills[sap] = (org, "SAP Integration")
    team_store.wbs_skills[uuid.UUID(project_id)] = [sap]

    listed = team_client.get(url, headers=headers).json()
    assert listed == [
        {"skillId": str(sap), "name": "SAP Integration", "required": True, "holders": []}
    ]

    team_client.post(
        f"/api/v1/projects/{project_id}/team",
        json={**SARA, "skills": ["Data Modeling"]},
        headers=headers,
    )
    response = team_client.post(
        url, json={"name": "data  modeling", "required": False}, headers=headers
    )
    assert response.status_code == 201, response.text
    modeling = response.json()
    assert (modeling["name"], modeling["required"], modeling["holders"]) == (
        "Data Modeling",
        False,
        ["Sara Kim"],
    )
    response = team_client.post(url, json={"name": "sap integration"}, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "sap integration is already in the list"
    assert team_client.post(url, json={"name": ""}, headers=headers).status_code == 422

    response = team_client.post(url, json={"name": "Terraform", "required": False}, headers=headers)
    terraform = response.json()["skillId"]
    response = team_client.patch(
        f"{url}/{terraform}", json={"name": "Terraform Cloud", "required": True}, headers=headers
    )
    assert (response.json()["name"], response.json()["required"]) == ("Terraform Cloud", True)
    response = team_client.patch(
        f"{url}/{terraform}", json={"name": "SAP integration"}, headers=headers
    )
    assert response.status_code == 409

    # Renaming to another organization skill points the project (and its holders) at it.
    spark = uuid.uuid4()
    team_store.skills[spark] = (org, "Spark / Kafka")
    response = team_client.patch(
        f"{url}/{modeling['skillId']}", json={"name": "spark / kafka"}, headers=headers
    )
    assert (response.json()["skillId"], response.json()["holders"]) == (str(spark), ["Sara Kim"])

    assert team_client.delete(f"{url}/{sap}", headers=headers).status_code == 204
    assert team_client.delete(f"{url}/{sap}", headers=headers).status_code == 404
    names = [skill["name"] for skill in team_client.get(url, headers=headers).json()]
    assert sorted(names) == ["Spark / Kafka", "Terraform Cloud"]


def test_clean_skill_names() -> None:
    """Skill names are trimmed, single-spaced and deduplicated ignoring case."""
    assert clean_skill_names([" SAP  Integration", "sap integration", "", "Kafka"]) == [
        "SAP Integration",
        "Kafka",
    ]


async def test_repo_members_average_booked_hours() -> None:
    """Members carry their rate, skills and average hours booked on other projects."""
    project = ProjectRef(uuid.uuid4(), uuid.uuid4(), "CRM", None, None, None)
    employee_id = uuid.uuid4()
    row = {
        "employee_id": employee_id,
        "full_name": "Ada",
        "role_title": "Engineer",
        "hours_per_week": 30,
        "total": 30,
    }
    conn = Conn(
        Rows([{**row, "is_contractor": False, "hourly_rate_cents": 9_000}]),
        Rows([{"employee_id": employee_id, "name": "Python"}]),
    )
    weeks = planning_weeks(date(2026, 11, 16), date(2026, 12, 31), date(2026, 11, 13))
    [member] = await TeamRepository(conn).members(project, weeks)  # type: ignore[arg-type]
    assert (member.booked_hours, member.hours_per_week, member.skills) == (4.5, 30.0, ["Python"])
    assert member.hourly_rate_cents == 9_000
    assert "employees.hourly_rate_cents" in conn.sql[0]
    assert "allocations.project_id != " in conn.sql[0]
    assert "FROM public.project_team_members" in conn.sql[0]
