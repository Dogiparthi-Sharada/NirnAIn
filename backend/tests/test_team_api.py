"""Team and skills endpoints with an in-memory fake repository."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import OrgRole
from app.domain.planning import planning_weeks
from app.domain.team import ProjectSkill, TeamMember, clean_skill_names
from app.repositories.access import ProjectRef
from app.repositories.team import TeamRepository
from tests.conftest import Store
from tests.test_optimization_repo import Rows
from tests.test_repositories import Conn

Auth = Callable[[uuid.UUID], dict[str, str]]


@dataclass
class TeamStore:
    employees: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    skills: dict[uuid.UUID, tuple[uuid.UUID, str]] = field(default_factory=dict)  # org, name
    held: set[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=set)  # employee, skill
    team: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = field(default_factory=dict)
    project_skills: dict[tuple[uuid.UUID, uuid.UUID], bool] = field(default_factory=dict)
    wbs_skills: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)  # project
    booked: dict[uuid.UUID, float] = field(default_factory=dict)
    applied: dict[uuid.UUID, int] = field(default_factory=dict)
    weeks: list[Sequence[date]] = field(default_factory=list)


class FakeTeam:
    def __init__(self, store: Store, t: TeamStore, user_id: uuid.UUID) -> None:
        self.s, self.t, self.uid = store, t, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        p = self.s.projects.get(project_id)
        if p is None or self._role(project_id) is None:
            return None
        return ProjectRef(p.id, p.org_id, p.name, p.start_date, p.target_end_date, p.budget_cents)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def applied_version(self, project_id: uuid.UUID) -> int | None:
        return self.t.applied.get(project_id)

    async def window(self, project: ProjectRef) -> tuple[date | None, date | None]:
        return project.start_date, project.target_end_date

    def _skill_names(self, employee_id: uuid.UUID) -> list[str]:
        return sorted(self.t.skills[s][1] for e, s in self.t.held if e == employee_id)

    async def members(self, project: ProjectRef, weeks: Sequence[date]) -> list[TeamMember]:
        self.t.weeks.append(weeks)
        return [
            TeamMember(
                employee_id=employee_id,
                full_name=self.t.employees[employee_id]["full_name"],
                role_title=row["role_title"],
                hours_per_week=row["hours_per_week"],
                hourly_rate_cents=self.t.employees[employee_id].get("hourly_rate_cents"),
                booked_hours=self.t.booked.get(employee_id, 0),
                skills=self._skill_names(employee_id),
            )
            for (project_id, employee_id), row in self.t.team.items()
            if project_id == project.id
        ]

    async def member_names(self, project_id: uuid.UUID) -> dict[uuid.UUID, str]:
        return {e: self.t.employees[e]["full_name"] for p, e in self.t.team if p == project_id}

    async def find_employee(self, org_id: uuid.UUID, full_name: str) -> uuid.UUID | None:
        return next(
            (
                i
                for i, e in self.t.employees.items()
                if e["org_id"] == org_id and e["full_name"].lower() == full_name.lower()
            ),
            None,
        )

    async def create_employee(
        self,
        org_id: uuid.UUID,
        full_name: str,
        role_title: str,
        hours_per_week: int,
        hourly_rate_cents: int | None,
    ) -> uuid.UUID:
        employee_id = uuid.uuid4()
        self.t.employees[employee_id] = {
            "org_id": org_id,
            "full_name": full_name,
            "role_title": role_title,
            "weekly_capacity_hours": hours_per_week,
            "hourly_rate_cents": hourly_rate_cents,
        }
        return employee_id

    async def set_hourly_rate(self, employee_id: uuid.UUID, hourly_rate_cents: int | None) -> None:
        self.t.employees[employee_id]["hourly_rate_cents"] = hourly_rate_cents

    async def rename_employee(self, employee_id: uuid.UUID, full_name: str) -> None:
        self.t.employees[employee_id]["full_name"] = full_name

    async def add_member(
        self, project_id: uuid.UUID, employee_id: uuid.UUID, role_title: str, hours_per_week: int
    ) -> None:
        self.t.team[(project_id, employee_id)] = {
            "role_title": role_title,
            "hours_per_week": hours_per_week,
        }

    async def update_member(
        self, project_id: uuid.UUID, employee_id: uuid.UUID, changes: dict[str, Any]
    ) -> bool:
        self.t.team[(project_id, employee_id)].update(changes)
        return True

    async def remove_member(self, project_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        return self.t.team.pop((project_id, employee_id), None) is not None

    async def ensure_skills(self, org_id: uuid.UUID, names: list[str]) -> list[uuid.UUID]:
        out = []
        for name in names:
            found = await self.org_skill(org_id, name)
            if found is None:
                found = uuid.uuid4()
                self.t.skills[found] = (org_id, name)
            out.append(found)
        return out

    async def add_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        self.t.held |= {(employee_id, s) for s in skill_ids}

    async def set_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        self.t.held = {(e, s) for e, s in self.t.held if e != employee_id}
        await self.add_employee_skills(employee_id, skill_ids)

    def _rows(self, project_id: uuid.UUID) -> list[tuple[uuid.UUID, bool]]:
        rows = [(s, r) for (p, s), r in self.t.project_skills.items() if p == project_id]
        return rows or [(s, True) for s in self.t.wbs_skills.get(project_id, [])]

    async def skills(self, project_id: uuid.UUID) -> list[ProjectSkill]:
        team = [e for p, e in self.t.team if p == project_id]
        return [
            ProjectSkill(
                skill_id=s,
                name=self.t.skills[s][1],
                required=r,
                holders=[self.t.employees[e]["full_name"] for e in team if (e, s) in self.t.held],
            )
            for s, r in self._rows(project_id)
        ]

    async def materialize_skills(self, project_id: uuid.UUID) -> None:
        for s, r in self._rows(project_id):
            self.t.project_skills[(project_id, s)] = r

    async def org_skill(self, org_id: uuid.UUID, name: str) -> uuid.UUID | None:
        return next(
            (i for i, (o, n) in self.t.skills.items() if o == org_id and n.lower() == name.lower()),
            None,
        )

    async def add_project_skill(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> None:
        self.t.project_skills[(project_id, skill_id)] = required

    async def set_required(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> bool:
        self.t.project_skills[(project_id, skill_id)] = required
        return True

    async def rename_skill(self, skill_id: uuid.UUID, name: str) -> None:
        self.t.skills[skill_id] = (self.t.skills[skill_id][0], name)

    async def replace_skill(
        self, project_id: uuid.UUID, old_id: uuid.UUID, new_id: uuid.UUID, required: bool
    ) -> None:
        del self.t.project_skills[(project_id, old_id)]
        self.t.project_skills[(project_id, new_id)] = required
        for p, e in list(self.t.team):
            if p == project_id and (e, old_id) in self.t.held:
                self.t.held.add((e, new_id))

    async def remove_skill(self, project_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
        return self.t.project_skills.pop((project_id, skill_id), None) is not None


@pytest.fixture
def team_store() -> TeamStore:
    return TeamStore()


@pytest.fixture
def team_client(app: FastAPI, store: Store, team_store: TeamStore) -> Iterator[TestClient]:
    def repo(principal: CurrentPrincipal) -> FakeTeam:
        return FakeTeam(store, team_store, principal.user_id)

    app.dependency_overrides[deps.get_team_repo] = repo
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID, str]:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    r = client.post(
        f"/api/v1/orgs/{org}/projects",
        json={"name": "CRM", "startDate": "2026-11-16", "targetEndDate": "2027-01-29"},
        headers=auth(user),
    )
    return user, org, r.json()["id"]


SARA = {"fullName": "Sara Kim", "roleTitle": "Lead Architect", "hoursPerWeek": 40, "skills": []}


def test_team_crud(
    team_client: TestClient, store: Store, team_store: TeamStore, auth: Auth
) -> None:
    user, org, pid = _project(team_client, store, auth)
    headers = auth(user)
    url = f"/api/v1/projects/{pid}/team"
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

    team_store.applied[uuid.UUID(pid)] = 1
    response = team_client.post(url, json=SARA, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "Plan v1 is in force; record a change on step 7"
    outsider = store.add_user("Eve")
    assert team_client.get(url, headers=auth(outsider)).status_code == 404


def test_team_hourly_rates(
    team_client: TestClient, store: Store, team_store: TeamStore, auth: Auth
) -> None:
    user, org, pid = _project(team_client, store, auth)
    headers = auth(user)
    url = f"/api/v1/projects/{pid}/team"
    response = team_client.post(url, json={**SARA, "hourlyRateCents": 9_500}, headers=headers)
    assert response.status_code == 201, response.text
    sara = response.json()["employeeId"]
    assert response.json()["hourlyRateCents"] == 9_500
    assert team_store.employees[uuid.UUID(sara)]["hourly_rate_cents"] == 9_500
    for bad in (-1, 100_000_001, 12.5, "lots"):
        response = team_client.post(
            url, json={**SARA, "fullName": "Ana", "hourlyRateCents": bad}, headers=headers
        )
        assert response.status_code == 422, bad

    # Reusing an organization employee keeps their rate unless the body gives one.
    known = uuid.uuid4()
    team_store.employees[known] = {"org_id": org, "full_name": "Leo", "hourly_rate_cents": 8_000}
    response = team_client.post(url, json={**SARA, "fullName": "Leo"}, headers=headers)
    assert response.json()["hourlyRateCents"] == 8_000
    member = f"{url}/{sara}"
    response = team_client.patch(member, json={"hourlyRateCents": 12_000}, headers=headers)
    assert (response.status_code, response.json()["hourlyRateCents"]) == (200, 12_000)
    response = team_client.patch(member, json={"hoursPerWeek": 20}, headers=headers)
    assert response.json()["hourlyRateCents"] == 12_000  # untouched when not sent
    response = team_client.patch(member, json={"hourlyRateCents": None}, headers=headers)
    assert (response.status_code, response.json()["hourlyRateCents"]) == (
        200,
        None,
    )  # back to the default
    assert (
        team_client.patch(member, json={"hourlyRateCents": -5}, headers=headers).status_code == 422
    )


def test_skills_crud(
    team_client: TestClient, store: Store, team_store: TeamStore, auth: Auth
) -> None:
    user, org, pid = _project(team_client, store, auth)
    h = auth(user)
    url = f"/api/v1/projects/{pid}/skills"
    sap = uuid.uuid4()
    team_store.skills[sap] = (org, "SAP Integration")
    team_store.wbs_skills[uuid.UUID(pid)] = [sap]

    listed = team_client.get(url, headers=h).json()
    assert listed == [
        {"skillId": str(sap), "name": "SAP Integration", "required": True, "holders": []}
    ]

    team_client.post(
        f"/api/v1/projects/{pid}/team", json={**SARA, "skills": ["Data Modeling"]}, headers=h
    )
    r = team_client.post(url, json={"name": "data  modeling", "required": False}, headers=h)
    assert r.status_code == 201, r.text
    modeling = r.json()
    assert (modeling["name"], modeling["required"], modeling["holders"]) == (
        "Data Modeling",
        False,
        ["Sara Kim"],
    )
    r = team_client.post(url, json={"name": "sap integration"}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"] == "sap integration is already in the list"
    assert team_client.post(url, json={"name": ""}, headers=h).status_code == 422

    r = team_client.post(url, json={"name": "Terraform", "required": False}, headers=h)
    terraform = r.json()["skillId"]
    r = team_client.patch(
        f"{url}/{terraform}", json={"name": "Terraform Cloud", "required": True}, headers=h
    )
    assert (r.json()["name"], r.json()["required"]) == ("Terraform Cloud", True)
    r = team_client.patch(f"{url}/{terraform}", json={"name": "SAP integration"}, headers=h)
    assert r.status_code == 409

    # Renaming to another organization skill points the project (and its holders) at it.
    spark = uuid.uuid4()
    team_store.skills[spark] = (org, "Spark / Kafka")
    r = team_client.patch(f"{url}/{modeling['skillId']}", json={"name": "spark / kafka"}, headers=h)
    assert (r.json()["skillId"], r.json()["holders"]) == (str(spark), ["Sara Kim"])

    assert team_client.delete(f"{url}/{sap}", headers=h).status_code == 204
    assert team_client.delete(f"{url}/{sap}", headers=h).status_code == 404
    names = [s["name"] for s in team_client.get(url, headers=h).json()]
    assert sorted(names) == ["Spark / Kafka", "Terraform Cloud"]

    team_store.applied[uuid.UUID(pid)] = 3
    assert team_client.delete(f"{url}/{spark}", headers=h).status_code == 409


def test_clean_skill_names() -> None:
    assert clean_skill_names([" SAP  Integration", "sap integration", "", "Kafka"]) == [
        "SAP Integration",
        "Kafka",
    ]


async def test_repo_members_average_booked_hours() -> None:
    project = ProjectRef(uuid.uuid4(), uuid.uuid4(), "CRM", None, None, None)
    emp = uuid.uuid4()
    row = {
        "employee_id": emp,
        "full_name": "Ada",
        "role_title": "Engineer",
        "hours_per_week": 30,
        "total": 30,
    }
    conn = Conn(
        Rows([{**row, "hourly_rate_cents": 9_000}]), Rows([{"employee_id": emp, "name": "Python"}])
    )
    weeks = planning_weeks(date(2026, 11, 16), date(2026, 12, 31), date(2026, 11, 13))
    [member] = await TeamRepository(conn).members(project, weeks)  # type: ignore[arg-type]
    assert (member.booked_hours, member.hours_per_week, member.skills) == (4.5, 30.0, ["Python"])
    assert member.hourly_rate_cents == 9_000
    assert "employees.hourly_rate_cents" in conn.sql[0]
    assert "allocations.project_id != " in conn.sql[0]
    assert "FROM public.project_team_members" in conn.sql[0]
