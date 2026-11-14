"""Moved test support."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from app.ai.wbs_generate import (
    PlannedDeliverable,
    PlannedDependency,
    PlannedPackage,
    WbsPlan,
)
from app.repositories.ai_jobs import AiJobStore
from tests.support.connections import Result, Rows


# --------------------------------------------------------------------------- WBS write SQL
class PlanConn:
    """Recording connection that answers write_plan's reads and fails one link."""

    def __init__(self, org_id: uuid.UUID, reject_link: int | None = None) -> None:
        self.org_id = org_id
        self.sql: list[str] = []
        self.params: list[Any] = []
        self.skills: dict[str, uuid.UUID] = {"salesforce": uuid.uuid4()}
        self.reject_link = reject_link
        self.links = 0
        self.savepoints = 0

    async def execute(self, stmt: Any, params: Any = None) -> Result:
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.sql.append(sql)
        self.params.append(params if params is not None else dict(compiled.params))
        if sql.startswith("SELECT public.projects.org_id"):
            return Rows([{"org_id": self.org_id, "name": "CRM"}])
        if sql.startswith("SELECT public.skills.id"):
            return Rows([{"id": i, "name": n} for n, i in self.skills.items()])
        if sql.startswith("INSERT INTO public.skills"):
            for key, value in compiled.params.items():
                if key.startswith("name"):
                    self.skills[str(value).casefold()] = uuid.uuid4()
        if sql.startswith("INSERT INTO public.dependencies"):
            self.links += 1
            if self.links == self.reject_link:
                raise DBAPIError("INSERT", {}, Exception("cycle"))
        return Rows([])

    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        self.savepoints += 1
        yield


def _plan() -> WbsPlan:
    def pkg(key: str, skills: tuple[str, ...]) -> PlannedPackage:
        return PlannedPackage(key, f"Package {key}", None, 10, skills, None)

    a, b, c = pkg("A", ("Salesforce", "QA")), pkg("B", ("qa",)), pkg("C", ())
    return WbsPlan(
        (PlannedDeliverable("Setup", None, None, (a, b, c)),),
        (PlannedDependency("A", "B"), PlannedDependency("B", "C"), PlannedDependency("A", "Z")),
    )


async def test_write_plan_writes_tree_skills_and_links() -> None:
    owner = uuid.uuid4()
    conn = PlanConn(uuid.uuid4(), reject_link=2)
    counts = await AiJobStore(conn).write_plan(uuid.uuid4(), _plan(), {"A": owner})  # type: ignore[arg-type]
    assert counts == {"deliverables": 1, "workPackages": 3, "dependencies": 1}
    inserts = [s.split(" (")[0] for s in conn.sql if s.startswith("INSERT")]
    assert inserts == [
        "INSERT INTO public.wbs_nodes",
        "INSERT INTO public.wbs_nodes",
        "INSERT INTO public.wbs_nodes",
        "INSERT INTO public.skills",
        "INSERT INTO public.task_skill_requirements",
        "INSERT INTO public.project_skills",
        "INSERT INTO public.dependencies",
        "INSERT INTO public.dependencies",
    ]
    root, level2, level3 = (
        p
        for s, p in zip(conn.sql, conn.params, strict=True)
        if s.startswith("INSERT INTO public.wbs_nodes")
    )
    assert [r["level"] for r in root + level2 + level3] == [1, 2, 3, 3, 3]
    assert root[0]["name"] == "CRM"
    assert all(r["origin"] == "ai" for r in root + level2 + level3)
    assert "estimate_confirmed_at" not in level3[0]
    assert [r["owner_employee_id"] for r in level3] == [owner, None, None]
    skill_sql = next(s for s in conn.sql if s.startswith("INSERT INTO public.skills"))
    assert "ON CONFLICT DO NOTHING" in skill_sql
    reqs = next(
        p
        for s, p in zip(conn.sql, conn.params, strict=True)
        if s.startswith("INSERT INTO public.task_skill")
    )
    assert len(reqs) == 3  # A: Salesforce + QA, B: QA (case-insensitive)
    assert {r["min_proficiency"] for r in reqs} == {2}
    upsert = next(s for s in conn.sql if s.startswith("INSERT INTO public.project_skills"))
    assert "ON CONFLICT (project_id, skill_id) DO UPDATE SET required" in upsert
    assert conn.savepoints == 2  # the dangling A -> Z link is skipped before the DB


async def test_write_plan_skips_when_a_wbs_exists() -> None:
    class Existing(PlanConn):
        async def execute(self, stmt: Any, params: Any = None) -> Result:
            result = await super().execute(stmt, params)
            if self.sql[-1].startswith("SELECT public.wbs_nodes.id"):
                return Rows([{"id": uuid.uuid4()}])
            return result

    conn = Existing(uuid.uuid4())
    assert await AiJobStore(conn).write_plan(uuid.uuid4(), _plan(), {}) is None  # type: ignore[arg-type]
    assert not any(s.startswith("INSERT") for s in conn.sql)
