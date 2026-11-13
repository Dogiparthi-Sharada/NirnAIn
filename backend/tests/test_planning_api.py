"""Planning endpoints (constraints, capacity, skills, dependencies) with in-memory fakes."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import OrgRole
from app.domain.planning import (
    CapacityEntry,
    ConstraintSet,
    ConstraintSetInput,
    Dependency,
    DependencyCreate,
    Milestone,
    PoolEmployee,
    average_weekly_hours,
    planning_weeks,
    skill_coverage,
)
from app.repositories.access import ProjectRef
from app.repositories.planning import PlanningRepository
from tests.conftest import Store
from tests.test_optimization_repo import Rows
from tests.test_repositories import Conn

Auth = Callable[[uuid.UUID], dict[str, str]]


def p0001(message: str) -> DBAPIError:
    class _OrigError(Exception):
        sqlstate = "P0001"

    orig = _OrigError()
    orig.message = message  # type: ignore[attr-defined]
    return DBAPIError("INSERT ...", {}, orig)


@dataclass
class PlanStore:
    constraints: dict[uuid.UUID, ConstraintSet] = field(default_factory=dict)
    nodes: dict[uuid.UUID, tuple[uuid.UUID, str]] = field(default_factory=dict)
    deps: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    team: list[PoolEmployee] = field(default_factory=list)
    required: dict[uuid.UUID, tuple[str, int]] = field(default_factory=dict)
    held: dict[uuid.UUID, tuple[str, int]] = field(default_factory=dict)
    weeks: list[Sequence[date]] = field(default_factory=list)


class FakePlanning:
    def __init__(self, store: Store, ps: PlanStore, user_id: uuid.UUID) -> None:
        self.s, self.ps, self.uid = store, ps, user_id

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

    async def constraints(self, project: ProjectRef) -> ConstraintSet:
        return self.ps.constraints.get(project.id) or ConstraintSet(
            budget_total_cents=project.budget_cents,
            contingency_pct=0,
            labor_cap_cents=None,
            infra_cap_cents=None,
            budget_mode="hard",
            start_date=project.start_date,
            must_finish_by=project.target_end_date,
            deadline_mode="hard",
            working_days_per_week=5,
            capacity_mode="soft",
            skill_mode="hard",
            milestones=[],
        )

    async def node_ids(self, project_id: uuid.UUID, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        return {i for i in ids if i in self.ps.nodes and self.ps.nodes[i][0] == project_id}

    async def save_constraints(self, project_id: uuid.UUID, body: ConstraintSetInput) -> None:
        self.ps.constraints[project_id] = ConstraintSet.model_validate(
            {
                **body.values(),
                "milestones": [
                    Milestone.model_validate(
                        {**m.model_dump(by_alias=False), "id": m.id or uuid.uuid4()}
                    )
                    for m in body.milestones
                ],
            }
        )

    async def capacity(self, project: ProjectRef, weeks: Sequence[date]) -> list[CapacityEntry]:
        self.ps.weeks.append(weeks)
        return [
            CapacityEntry.model_validate({**e.model_dump(by_alias=False), "allocated_hours": 7.5})
            for e in self.ps.team
        ]

    async def employees(self, org_id: uuid.UUID) -> list[PoolEmployee]:
        return self.ps.team

    async def required_skills(self, project_id: uuid.UUID) -> dict[uuid.UUID, tuple[str, int]]:
        return self.ps.required

    async def held_skills(self, org_id: uuid.UUID) -> dict[uuid.UUID, tuple[str, int]]:
        return self.ps.held

    def _label(self, node_id: uuid.UUID) -> str:
        return self.ps.nodes[node_id][1]

    async def dependencies(self, project_id: uuid.UUID) -> list[Dependency]:
        return [
            d
            for dep_id, row in self.ps.deps.items()
            if row["project_id"] == project_id and (d := await self.dependency(dep_id)) is not None
        ]

    async def dependency_project(self, dependency_id: uuid.UUID) -> uuid.UUID | None:
        row = self.ps.deps.get(dependency_id)
        if row is None or self._role(row["project_id"]) is None:
            return None
        return uuid.UUID(str(row["project_id"]))

    async def dependency(self, dependency_id: uuid.UUID) -> Dependency | None:
        row = self.ps.deps.get(dependency_id)
        if row is None:
            return None
        return Dependency.model_validate(
            {
                **row,
                "predecessor_label": self._label(row["predecessor_id"]),
                "successor_label": self._label(row["successor_id"]),
            }
        )

    async def create_dependency(self, project_id: uuid.UUID, body: DependencyCreate) -> uuid.UUID:
        for node in (body.predecessor_id, body.successor_id):
            if self.ps.nodes.get(node, (None, ""))[0] != project_id:
                raise p0001("Dependency tasks must belong to the dependency's project")
        dep_id = uuid.uuid4()
        self.ps.deps[dep_id] = {
            "id": dep_id,
            "project_id": project_id,
            **body.model_dump(by_alias=False),
            "origin": "manual",
        }
        return dep_id

    async def update_dependency(self, dependency_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        self.ps.deps[dependency_id].update(changes)
        return True

    async def delete_dependency(self, dependency_id: uuid.UUID) -> bool:
        return self.ps.deps.pop(dependency_id, None) is not None


@pytest.fixture
def plan_store() -> PlanStore:
    return PlanStore()


@pytest.fixture
def plan_client(app: FastAPI, store: Store, plan_store: PlanStore) -> Iterator[TestClient]:
    def repo(principal: CurrentPrincipal) -> FakePlanning:
        return FakePlanning(store, plan_store, principal.user_id)

    app.dependency_overrides[deps.get_planning_repo] = repo
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _project(
    client: TestClient, store: Store, auth: Auth, **body: Any
) -> tuple[uuid.UUID, uuid.UUID, str]:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    r = client.post(
        f"/api/v1/orgs/{org}/projects", json={"name": "CRM", **body}, headers=auth(user)
    )
    return user, org, r.json()["id"]


CONSTRAINTS: dict[str, Any] = {
    "budgetTotalCents": 4_000_000,
    "contingencyPct": 12.5,
    "laborCapCents": None,
    "infraCapCents": 100_000,
    "budgetMode": "soft",
    "startDate": "2026-11-16",
    "mustFinishBy": "2026-12-31",
    "deadlineMode": "hard",
    "workingDaysPerWeek": 5,
    "capacityMode": "soft",
    "skillMode": "hard",
    "milestones": [{"name": "Go-live", "dueDate": "2026-12-15", "mode": "hard"}],
}


def test_constraints_defaults_and_put(plan_client: TestClient, store: Store, auth: Auth) -> None:
    user, org, pid = _project(
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


def test_capacity_uses_planning_window(
    plan_client: TestClient, store: Store, plan_store: PlanStore, auth: Auth
) -> None:
    user, _, pid = _project(
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
    user, org, pid = _project(plan_client, store, auth)
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


def test_dependencies_crud(
    plan_client: TestClient, store: Store, plan_store: PlanStore, auth: Auth
) -> None:
    user, org, pid = _project(plan_client, store, auth)
    h = auth(user)
    a, b = uuid.uuid4(), uuid.uuid4()
    plan_store.nodes = {a: (uuid.UUID(pid), "1.1 Design"), b: (uuid.UUID(pid), "1.2 Build")}
    url = f"/api/v1/projects/{pid}/dependencies"

    r = plan_client.post(url, json={"predecessorId": str(a), "successorId": str(b)}, headers=h)
    assert r.status_code == 201, r.text
    dep = r.json()
    assert dep == {
        "id": dep["id"],
        "predecessorId": str(a),
        "successorId": str(b),
        "predecessorLabel": "1.1 Design",
        "successorLabel": "1.2 Build",
        "type": "FS",
        "lagDays": 0,
        "origin": "manual",
    }
    assert plan_client.get(url, headers=h).json() == [dep]

    r = plan_client.patch(
        f"/api/v1/dependencies/{dep['id']}", json={"type": "SS", "lagDays": 2}, headers=h
    )
    assert (r.status_code, r.json()["type"], r.json()["lagDays"]) == (200, "SS", 2)
    assert (
        plan_client.patch(f"/api/v1/dependencies/{dep['id']}", json={}, headers=h).status_code
        == 422
    )
    assert (
        plan_client.patch(
            f"/api/v1/dependencies/{dep['id']}", json={"type": None}, headers=h
        ).status_code
        == 422
    )

    r = plan_client.post(
        url, json={"predecessorId": str(a), "successorId": str(uuid.uuid4())}, headers=h
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "Dependency tasks must belong to the dependency's project"
    r = plan_client.post(url, json={"predecessorId": str(a), "successorId": str(a)}, headers=h)
    assert r.status_code == 422
    r = plan_client.post(
        url, json={"predecessorId": str(a), "successorId": str(b), "type": "XX"}, headers=h
    )
    assert r.status_code == 422

    viewer = store.add_user("Vic")
    store.members[(org, viewer)] = OrgRole.VIEWER
    hv = auth(viewer)
    assert plan_client.get(url, headers=hv).status_code == 200
    assert (
        plan_client.post(
            url, json={"predecessorId": str(b), "successorId": str(a)}, headers=hv
        ).status_code
        == 403
    )
    assert plan_client.delete(f"/api/v1/dependencies/{dep['id']}", headers=hv).status_code == 403

    assert plan_client.delete(f"/api/v1/dependencies/{dep['id']}", headers=h).status_code == 204
    assert plan_client.delete(f"/api/v1/dependencies/{dep['id']}", headers=h).status_code == 404
    assert plan_client.get(url, headers=h).json() == []


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
