"""Optimization repository SQL, compiled for PostgreSQL against a recording connection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.common import SolverStatus
from app.optimization.explain import build_report
from app.optimization.model import Constraints, PlanningInput, Task
from app.optimization.solver import solve
from app.repositories.optimization import (
    ActiveRunExistsError,
    OptimizationRepository,
    ProjectContext,
)
from app.repositories.optimization_store import OptimizationJobStore
from tests.support.connections import Conn, Result, Rows, project_context
from tests.support.solver_inputs import FAST, make_input

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def run_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "status": "optimal",
        "solver": "OR-Tools CP-SAT 9.15.6755",
        "runtime_ms": 4300,
        "error": None,
        "infeasibility": None,
        "result": None,
        "created_at": NOW,
        "started_at": NOW,
        "finished_at": NOW,
        "version": None,
        "applied_at": None,
        "options": {},
    }
    row.update(overrides)
    return row


async def test_project_and_can_write() -> None:
    ctx = project_context()
    conn = Conn(Rows([_ctx_dict(ctx)]), Rows([{"x": True}]))
    repo = OptimizationRepository(conn)  # type: ignore[arg-type]
    assert await repo.project(ctx.id) == ctx
    assert await repo.can_write(ctx.id) is True
    assert "private.can_write_project" in conn.sql[1]
    assert await OptimizationRepository(Conn()).project(ctx.id) is None  # type: ignore[arg-type]


def _ctx_dict(ctx: ProjectContext) -> dict[str, Any]:
    return {k: getattr(ctx, k) for k in ProjectContext.__slots__}


async def test_active_run_and_fail() -> None:
    rid = uuid.uuid4()
    conn = Conn(Rows([{"id": rid, "stale": False}]), Rows([]))
    repo = OptimizationRepository(conn)  # type: ignore[arg-type]
    active = await repo.active_run(uuid.uuid4(), 180)
    assert active is not None
    assert (active.id, active.stale) == (rid, False)
    assert "make_interval" in conn.sql[0]
    assert "public.optimization_runs.status IN (" in conn.sql[0]
    await repo.fail_run(rid, "x")
    assert conn.sql[1].startswith("UPDATE public.optimization_runs SET status=")
    assert await repo.active_run(uuid.uuid4(), 180) is None


async def test_get_and_latest_map_columns() -> None:
    row = run_row(
        infeasibility=[{"name": "n", "explanation": "e"}],
        status="infeasible",
        version=2,
        applied_at=NOW,
        options={"remedy": "specialist"},
    )
    queued = run_row(runtime_ms=None, status="queued", options=None, version=2, applied_at=NOW)
    conn = Conn(Rows([row]), Rows([queued]), Rows([]))
    repo = OptimizationRepository(conn)  # type: ignore[arg-type]
    run = await repo.get(row["project_id"], row["id"])
    assert run is not None
    assert run.solve_seconds == 4.3
    assert run.status == SolverStatus.INFEASIBLE
    assert run.iis is not None
    assert run.iis[0].name == "n"
    assert (run.version, run.applied_at, run.options) == (2, NOW, {"remedy": "specialist"})
    latest = await repo.latest(uuid.uuid4())
    assert latest is not None
    assert latest.solve_seconds is None
    assert (latest.version, latest.applied_at, latest.options) == (2, NOW, {})
    assert "ORDER BY public.optimization_runs.created_at DESC" in conn.sql[1]
    assert await repo.latest(uuid.uuid4()) is None


class _UniqueError(Exception):
    sqlstate = "23505"


class _OtherError(Exception):
    sqlstate = "23503"


class RaisingConn(Conn):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    async def execute(self, stmt: Any) -> Result:
        raise IntegrityError("INSERT", {}, self.exc)


async def test_create_maps_unique_violation() -> None:
    conn = Conn()
    rid = await OptimizationRepository(conn).create(uuid.uuid4(), {"version": 1})  # type: ignore[arg-type]
    assert isinstance(rid, uuid.UUID)
    assert conn.sql[0].startswith("INSERT INTO public.optimization_runs")
    with pytest.raises(ActiveRunExistsError):
        await OptimizationRepository(RaisingConn(_UniqueError())).create(uuid.uuid4(), {})  # type: ignore[arg-type]
    with pytest.raises(IntegrityError):
        await OptimizationRepository(RaisingConn(_OtherError())).create(uuid.uuid4(), {})  # type: ignore[arg-type]


async def test_baseline_owners_and_change_requests() -> None:
    run_id, change_id, node, person = (uuid.uuid4() for _ in range(4))
    conn = Conn(
        Rows([{"id": run_id}]),
        Rows([{"wbs_node_id": node, "employee_id": person}]),
        Rows([]),
        Rows([{"status": "ready"}]),
        Rows([]),
    )
    repo = OptimizationRepository(conn)  # type: ignore[arg-type]
    project_id = uuid.uuid4()
    assert await repo.baseline_owners(project_id, run_id) == {str(node): str(person)}
    assert "planned_assignments.employee_id IS NOT NULL" in conn.sql[1]
    assert await repo.baseline_owners(project_id, uuid.uuid4()) is None
    assert await repo.change_request_status(project_id, change_id) == "ready"
    await repo.mark_replanning(project_id, change_id, run_id)
    assert conn.sql[4].startswith("UPDATE public.change_requests SET status=")
    assert "proposed_run_id=" in conn.sql[4]


async def test_create_stores_options() -> None:
    conn = Conn(Rows([]))
    repo = OptimizationRepository(conn)  # type: ignore[arg-type]
    await repo.create(uuid.uuid4(), {"version": 1}, {"remedy": "specialist"})
    assert "options" in conn.sql[0]
    assert conn.params[0]["options"] == {"remedy": "specialist"}


async def test_job_store_claim_finish_fail() -> None:
    snap = make_input().to_snapshot()
    conn = Conn(Rows([{"input_snapshot": snap}]), Rows([]), Rows([]), Rows([]), Rows([]))
    store = OptimizationJobStore(conn)  # type: ignore[arg-type]
    rid = uuid.uuid4()
    assert await store.claim(rid) == snap
    assert "RETURNING public.optimization_runs.input_snapshot" in conn.sql[0]

    uuid_inp = _with_uuid_ids(make_input())
    report = build_report(uuid_inp, solve(uuid_inp, FAST))
    await store.finish(rid, report)
    assert conn.sql[1].startswith("UPDATE public.optimization_runs SET status=")
    assert "result=" in conn.sql[1]
    assert conn.sql[2].startswith("INSERT INTO public.planned_assignments")
    await store.fail(rid, "nope")
    assert "status IN" in conn.sql[3]
    assert await OptimizationJobStore(Conn()).claim(rid) is None  # type: ignore[arg-type]


async def test_job_store_finish_with_recommendations_and_stale() -> None:
    from app.optimization.model import SkillRequirement

    inp = _with_uuid_ids(
        make_input(
            tasks=(
                Task(
                    "b",
                    "1.2",
                    "Build",
                    5,
                    owner_id="e1",
                    skills=(SkillRequirement("sap", "SAP", 4),),
                ),
            ),
            dependencies=(),
            constraints=Constraints(),
        )
    )
    report = build_report(inp, solve(inp, FAST))
    assert report.recommendations
    conn = Conn(Rows([]), Rows([]), Rows([]), Rows([], rowcount=3))
    store = OptimizationJobStore(conn)  # type: ignore[arg-type]
    await store.finish(uuid.uuid4(), report)
    assert conn.sql[2].startswith("INSERT INTO public.recommendations")
    assert await store.fail_stale(180, "x") == 3
    assert "make_interval" in conn.sql[3]


def _with_uuid_ids(inp: PlanningInput) -> PlanningInput:
    """Rewrite task/employee ids as UUIDs (the DB rows need them)."""
    import json

    snap = inp.to_snapshot()
    ids = {t["id"] for t in snap["tasks"]} | {e["id"] for e in snap["employees"]}
    text = json.dumps(snap)
    for old in ids:
        text = text.replace(f'"{old}"', f'"{uuid.uuid4()}"')
    return PlanningInput.from_snapshot(json.loads(text))
