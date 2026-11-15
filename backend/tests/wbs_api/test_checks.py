"""Tests of the free built-in WBS checks and of what they read.

Purpose: Prove `GET …/wbs/checks` re-runs the checks on the current WBS (with its change time) for
anyone who can see the project, and that the shared reader takes dates, team, skills and the
specialist answer the way the optimizer does.
How it fits: Exercises app/api/v1/wbs.py with a fake checks repository and
app/repositories/wbs_checks.py with a scripted connection; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.ai.wbs_validate import NodeInfo, ValidationInput
from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import OrgRole
from app.repositories.wbs_checks import read_validation_input, wbs_changed_at
from tests.support.projects import create_project
from tests.support.store import Store
from tests.support.tokens import Auth
from tests.support.wbs import FakeWbs, WbsStore, add_node

CHANGED = datetime(2026, 11, 14, 21, 5, tzinfo=UTC)


class FakeChecks(FakeWbs):
    """The WBS fake plus the checks' input, built from the nodes as they are now."""

    async def check_input(self, project_id: uuid.UUID, today: date) -> ValidationInput | None:
        """Return the checks' input built from the fake WBS, or None for a hidden project."""
        if await self.project(project_id) is None:
            return None
        nodes = tuple(
            NodeInfo(
                node.id,
                node.parent_id,
                node.level,
                node.code,
                node.name,
                node.duration_days,
                node.owner_employee_id,
            )
            for node in await self.tree(project_id)
        )
        return ValidationInput("CRM", nodes, today=today)

    async def changed_at(self, project_id: uuid.UUID) -> datetime | None:
        """Return the fixed change time once the project has nodes."""
        return CHANGED if await self.tree(project_id) else None


@pytest.fixture
def checks_store() -> WbsStore:
    """Provide empty fake WBS data for the checks."""
    return WbsStore()


@pytest.fixture
def checks_client(app: FastAPI, store: Store, checks_store: WbsStore) -> Iterator[TestClient]:
    """Provide a test client whose WBS repository is the checks fake."""

    def repo(principal: CurrentPrincipal) -> FakeChecks:
        """Return the checks fake acting as the caller."""
        return FakeChecks(store, checks_store, principal.user_id)

    app.dependency_overrides[deps.get_wbs_repo] = repo
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_checks_follow_the_current_wbs(
    checks_client: TestClient, store: Store, checks_store: WbsStore, auth: Auth
) -> None:
    """Checks read the current WBS: an empty one is missing work, then issues follow edits."""
    user, org, project_id = create_project(checks_client, store, auth)
    headers = auth(user)
    url = f"/api/v1/projects/{project_id}/wbs/checks"
    empty = checks_client.get(url, headers=headers).json()
    assert set(empty) == {"issues", "checkedAt", "wbsChangedAt"}
    assert empty["wbsChangedAt"] is None
    assert [issue["type"] for issue in empty["issues"]] == ["missing"]

    root = add_node(checks_client, project_id, headers, name="CRM")
    deliverable = add_node(checks_client, project_id, headers, parentId=root["id"], name="Build")
    package = add_node(checks_client, project_id, headers, parentId=deliverable["id"], name="API")
    body = checks_client.get(url, headers=headers).json()
    assert body["issues"] == [{"type": "warning", "text": "1.1.1 API has no duration."}]
    assert body["wbsChangedAt"] == "2026-11-14T21:05:00Z"
    assert datetime.fromisoformat(body["checkedAt"]).date() == datetime.now(UTC).date()

    # Entering the estimate clears the finding on the next call: no job to re-run. An
    # owner is optional on step 4 (no team yet): assigning one changes nothing.
    employee = uuid.uuid4()
    checks_store.employees[employee] = (org, "Mark Chen", True)
    patched = checks_client.patch(
        f"/api/v1/wbs/nodes/{package['id']}",
        json={"durationDays": 5, "ownerEmployeeId": str(employee)},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert checks_client.get(url, headers=headers).json()["issues"] == []


def test_checks_need_a_visible_project(checks_client: TestClient, store: Store, auth: Auth) -> None:
    """Viewers may run the checks; hidden projects are 404 and anonymous calls 401."""
    _, org, project_id = create_project(checks_client, store, auth)
    url = f"/api/v1/projects/{project_id}/wbs/checks"
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    assert checks_client.get(url, headers=auth(viewer)).status_code == 200
    outsider = store.add_user("Eve")
    missing = checks_client.get(url, headers=auth(outsider))
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert checks_client.get(url).status_code == 401


# --------------------------------------------------------------------------- input SQL
class Answer:
    """Rows with attribute access, `.scalar()` and `.scalars()`, like SQLAlchemy results."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        """Wrap the rows with attribute access and `_mapping`."""
        self._rows = [SimpleNamespace(_mapping=row, **row) for row in rows]

    def first(self) -> Any:
        """Return the first row, or None."""
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        """Return every row."""
        return self._rows

    def scalar(self) -> Any:
        """Return the first column of the first row, or None."""
        return next(iter(self._rows[0]._mapping.values())) if self._rows else None

    def scalars(self) -> SimpleNamespace:
        """Return an object whose `all()` gives each row's first column."""
        values = [next(iter(row._mapping.values())) for row in self._rows]
        return SimpleNamespace(all=lambda: values)


class Scripted:
    """Recording connection that answers each query by how its SQL starts."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        """Answer each query by the scripted rows of its SQL prefix."""
        self.answers = answers
        self.sql: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, stmt: Any) -> Answer:
        """Record the compiled statement and return the rows scripted for its prefix."""
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = " ".join(str(compiled).split())
        self.sql.append(sql)
        self.params.append(dict(compiled.params))
        rows = next((rows for prefix, rows in self.answers.items() if sql.startswith(prefix)), [])
        return Answer(rows)

    def query(self, prefix: str) -> tuple[str, dict[str, Any]]:
        """Return the recorded SQL and parameters of the query starting with `prefix`."""
        return next(
            (sql, params)
            for sql, params in zip(self.sql, self.params, strict=True)
            if sql.startswith(prefix)
        )


PROJECT = "SELECT public.projects.name"
TEAM = "SELECT public.project_team_members.employee_id"
HELD = "SELECT DISTINCT public.skills.name"
REQUIRED = "SELECT public.skills.name"


def _project_row() -> dict[str, Any]:
    """Return the project row the reader joins with its constraint set."""
    return {
        "name": "CRM",
        "start_date": date(2026, 11, 2),
        "deadline": date(2026, 11, 30),
        "per_week": 5,
        "specialist_answer": None,
        "specialist_rate_confirmed": None,
        "contractor_rate_cents": None,
    }


async def test_input_uses_the_project_team_when_it_has_members() -> None:
    """With a team, the input carries its ids and skills, the dates and the required skills."""
    member = uuid.uuid4()
    conn = Scripted(
        {
            PROJECT: [_project_row()],
            TEAM: [{"employee_id": member}],
            HELD: [{"name": "Python"}, {"name": " SAP   Integration "}],
            REQUIRED: [{"name": "Python"}, {"name": "QA"}],
        }
    )
    project_id = uuid.uuid4()
    checks_input = await read_validation_input(conn, project_id, date(2026, 11, 16))  # type: ignore[arg-type]
    assert checks_input is not None
    assert (checks_input.team_ids, checks_input.held_skills) == (
        frozenset({member}),
        frozenset({"python", "sap integration"}),
    )
    assert (checks_input.today, checks_input.start_date, checks_input.deadline) == (
        date(2026, 11, 16),
        date(2026, 11, 2),
        date(2026, 11, 30),
    )
    assert checks_input.required_skills == ("Python", "QA")
    assert checks_input.scope == ()  # only the AI review reads the scope
    held_sql, held_params = conn.query(HELD)
    assert [member] in held_params.values()
    assert "public.employee_skills.proficiency >" in held_sql
    # Every other query names the project: the AI job reads without RLS.
    for sql, params in zip(conn.sql, conn.params, strict=True):
        if sql != held_sql:
            assert project_id in params.values(), sql


async def test_input_without_a_team_reads_no_people() -> None:
    """Without a team, no people or skills are read and the required skills still are."""
    conn = Scripted({PROJECT: [_project_row()], REQUIRED: [{"name": "RBAC"}]})
    checks_input = await read_validation_input(conn, uuid.uuid4(), None)  # type: ignore[arg-type]
    assert checks_input is not None
    assert (checks_input.has_team, checks_input.held_skills, checks_input.required_skills) == (
        False,
        frozenset(),
        ("RBAC",),
    )
    # Step 4 never checks skills against the whole organization: no one's skills are read.
    assert not any("employee_skills" in sql for sql in conn.sql)


async def test_input_reads_the_specialist_answer_and_the_confirmed_rate() -> None:
    """Specialists are allowed on 'yes'; their rate counts only once confirmed ($175/h default)."""
    cases = [
        (("yes", True, 15_000), (True, 15_000)),
        (("yes", True, None), (True, 17_500)),  # the $175/h estimate, confirmed
        (("yes", False, 15_000), (True, None)),  # a rate nobody confirmed isn't named
        (("no", True, 15_000), (False, 15_000)),
        (("unknown", False, None), (False, None)),
        ((None, None, None), (False, None)),  # no constraint set yet
    ]
    for (answer, confirmed, rate), expected in cases:
        row = {
            **_project_row(),
            "specialist_answer": answer,
            "specialist_rate_confirmed": confirmed,
            "contractor_rate_cents": rate,
        }
        checks_input = await read_validation_input(Scripted({PROJECT: [row]}), uuid.uuid4(), None)  # type: ignore[arg-type]
        assert checks_input is not None
        assert (checks_input.specialists_allowed, checks_input.specialist_rate_cents) == expected, (
            row
        )


async def test_input_of_a_missing_project_and_the_change_time() -> None:
    """A hidden project reads nothing more; the change time is the latest node update."""
    conn = Scripted({})
    assert await read_validation_input(conn, uuid.uuid4(), date(2026, 11, 16)) is None  # type: ignore[arg-type]
    assert len(conn.sql) == 1

    project_id = uuid.uuid4()
    conn = Scripted({"SELECT max(public.wbs_nodes.updated_at)": [{"max_1": CHANGED}]})
    assert await wbs_changed_at(conn, project_id) == CHANGED  # type: ignore[arg-type]
    assert "WHERE public.wbs_nodes.project_id =" in conn.sql[0]
    assert list(conn.params[0].values()) == [project_id]
