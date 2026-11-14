"""Optimization repository SQL, compiled for PostgreSQL against a recording connection."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.common import SolverStatus
from app.optimization.explain import build_report
from app.optimization.model import Constraints, PlanningInput, Task
from app.optimization.solver import solve
from app.repositories.optimization import (
    ActiveRunExistsError,
    OptimizationJobStore,
    OptimizationRepository,
    ProjectContext,
)
from app.repositories.planning_input import build_constraints
from tests.test_optimization_solver import FAST, make_input
from tests.test_repositories import Conn, Result

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


class Rows(Result):
    """Result whose rows support attribute access, like SQLAlchemy rows."""

    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 1) -> None:
        super().__init__(rows, rowcount)
        self._rows = [_Row(r) for r in rows]

    def scalar(self) -> Any:
        return next(iter(self._rows[0]._mapping.values())) if self._rows else None


class _Row(SimpleNamespace):
    def __init__(self, mapping: dict[str, Any]) -> None:
        super().__init__(**mapping)
        self._mapping = mapping


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


def project_ctx() -> ProjectContext:
    return ProjectContext(
        uuid.uuid4(), uuid.uuid4(), "CRM", date(2026, 10, 5), date(2027, 1, 29), 37_500_000
    )


async def test_project_and_can_write() -> None:
    ctx = project_ctx()
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


async def test_load_input_builds_planning_input() -> None:
    ctx = project_ctx()
    root, d1, wp1, wp2, d2 = (uuid.uuid4() for _ in range(5))
    e1, e2, sap = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    def node(
        id_: uuid.UUID, parent: uuid.UUID | None, level: int, code: str, **kw: Any
    ) -> dict[str, Any]:
        base = {
            "id": id_,
            "parent_id": parent,
            "level": level,
            "name": f"Node {code}",
            "duration_days": None,
            "effort_hours": None,
            "owner_employee_id": None,
            "code": code,
        }
        base.update(kw)
        return base

    conn = Conn(
        Rows(
            [
                node(root, None, 1, "1"),
                node(d1, root, 2, "1.1"),
                node(
                    wp1,
                    d1,
                    3,
                    "1.1.1",
                    duration_days=5,
                    effort_hours=Decimal("30.0"),
                    owner_employee_id=e1,
                ),
                node(wp2, d1, 3, "1.1.2", duration_days=0),  # no duration: not a work package
                node(d2, root, 2, "1.2", duration_days=10),  # childless level 2: work package
            ]
        ),
        Rows([{"wbs_node_id": wp1, "skill_id": sap, "name": "SAP", "min_proficiency": 3}]),
        Rows(
            [
                {
                    "id": e1,
                    "full_name": "Mark",
                    "hours_per_week": Decimal("40.0"),
                    "hourly_rate_cents": 9000,
                    "is_contractor": False,
                },
                {
                    "id": e2,
                    "full_name": "Lisa",
                    "hours_per_week": Decimal("16.0"),  # hours on this project
                    "hourly_rate_cents": None,
                    "is_contractor": True,
                },
            ]
        ),
        Rows([{"employee_id": e1, "skill_id": sap, "proficiency": 4}]),
        # dependency on the summary node d1 expands to its work package wp1
        Rows([{"predecessor_id": d1, "successor_id": d2, "type": "FS", "lag_days": 2}]),
        Rows([]),  # no constraint set -> project fallbacks
        Rows(
            [
                {
                    "id": uuid.uuid4(),
                    "name": "Phase 1",
                    "due_date": date(2026, 11, 2),
                    "wbs_node_id": d1,
                    "mode": "hard",
                },
                {
                    "id": uuid.uuid4(),
                    "name": "Kickoff",
                    "due_date": date(2026, 10, 5),
                    "wbs_node_id": None,
                    "mode": "soft",
                },
            ]
        ),
        Rows([{"employee_id": e2, "week_start": date(2026, 10, 12), "hours": Decimal("16.0")}]),
    )
    inp = await OptimizationRepository(conn).load_input(ctx, date(2026, 10, 1))  # type: ignore[arg-type]
    assert [t.code for t in inp.tasks] == ["1.1.1", "1.2"]
    t1 = inp.tasks[0]
    assert (t1.effort_hours, t1.owner_id) == (30.0, str(e1))
    assert t1.skills[0].skill_name == "SAP"
    assert [e.name for e in inp.employees] == ["Mark", "Lisa"]
    assert [employee.weekly_capacity_hours for employee in inp.employees] == [40.0, 16.0]
    assert "project_team_members" in conn.sql[2]  # the project's team, not the whole org
    assert inp.deliverables == {"1.1": "Node 1.1", "1.2": "Node 1.2"}
    assert inp.constraints.allow_contractors is False  # nothing answered yet
    assert inp.employees[0].skills == {str(sap): 4}
    assert inp.employees[1].is_contractor
    assert [(d.predecessor_id, d.successor_id, d.lag_days) for d in inp.dependencies] == [
        (str(wp1), str(d2), 2)
    ]
    assert inp.start_date == date(2026, 10, 5)
    assert inp.constraints.budget_total_cents == 37_500_000
    assert inp.constraints.must_finish_by == date(2027, 1, 29)
    assert [m.name for m in inp.milestones] == ["Phase 1"]  # nodeless milestone is informational
    assert inp.milestones[0].task_ids == (str(wp1),)
    assert inp.allocations[0].hours == 16.0
    assert "wbs_nodes_with_code" in conn.sql[0]
    assert "allocations.project_id != " in conn.sql[-1]
    # The snapshot survives a round trip.
    assert PlanningInput.from_snapshot(inp.to_snapshot()) == inp


async def test_load_input_with_constraint_set_and_no_data() -> None:
    cs = {
        "project_id": uuid.uuid4(),
        "budget_total_cents": None,
        "labor_cap_cents": 100,
        "budget_mode": "soft",
        "budget_penalty": 10.0,
        "start_date": date(2026, 11, 2),
        "must_finish_by": None,
        "deadline_mode": "soft",
        "deadline_penalty_per_day": 5.0,
        "working_days_per_week": 6,
        "capacity_mode": "hard",
        "capacity_penalty": 1.0,
        "skill_mode": "soft",
        "allow_contractors": False,
        "contractor_rate_cents": None,
        "specialist_answer": "yes",
        "deadline_answer": "soft",
    }
    # nodes, project team (empty), org employees (fallback), deps, constraint set,
    # milestones (no task ids -> no skills/profs/alloc queries)
    conn = Conn(Rows([]), Rows([]), Rows([]), Rows([]), Rows([cs]), Rows([]))
    ctx = project_ctx()
    inp = await OptimizationRepository(conn).load_input(ctx, date(2026, 10, 1))  # type: ignore[arg-type]
    assert (inp.tasks, inp.employees) == ((), ())
    c = inp.constraints
    assert inp.start_date == date(2026, 11, 2)
    assert c.budget_total_cents == ctx.budget_cents
    assert c.must_finish_by == ctx.target_end_date
    assert (c.labor_cap_cents, c.budget_mode, c.working_days_per_week) == (100, "soft", 6)
    assert (c.capacity_mode, c.skill_mode, c.deadline_mode) == ("hard", "soft", "soft")
    assert (c.allow_contractors, c.contractor_rate_cents) == (True, 17_500)  # default rate
    assert "project_team_members" in conn.sql[1]
    assert "employees.active IS true" in conn.sql[2]  # no project team: whole org
    assert len(conn.sql) == 6


@pytest.mark.parametrize(
    ("deadline_answer", "specialist_answer", "deadline_mode", "contractors"),
    [
        (None, None, "hard", False),
        ("unknown", "unknown", "hard", False),
        ("hard", "no", "hard", False),
        ("soft", "yes", "soft", True),
    ],
)
def test_answers_set_deadline_and_specialist_policy(
    deadline_answer: str | None,
    specialist_answer: str | None,
    deadline_mode: str,
    contractors: bool,
) -> None:
    constraint_set: Any = SimpleNamespace(
        start_date=None,
        budget_total_cents=None,
        labor_cap_cents=None,
        budget_mode="soft",
        budget_penalty=1.0,
        must_finish_by=date(2026, 12, 18),
        deadline_mode="soft",  # ignored: the answer decides
        deadline_penalty_per_day=1.0,
        working_days_per_week=5,
        capacity_mode="hard",
        capacity_penalty=1.0,
        skill_mode="soft",
        allow_contractors=True,  # ignored: only a "yes" allows specialists
        contractor_rate_cents=9_000,
        deadline_answer=deadline_answer,
        specialist_answer=specialist_answer,
    )
    constraints, _ = build_constraints(constraint_set, project_ctx(), date(2026, 10, 1))
    assert constraints.deadline_mode == deadline_mode
    assert constraints.allow_contractors is contractors
    assert constraints.contractor_rate_cents == 9_000
    assert (constraints.budget_mode, constraints.capacity_mode) == ("soft", "hard")


async def test_input_facts_reads_answers_and_confirmations() -> None:
    ctx = project_ctx()
    inp = make_input(
        tasks=(Task(str(uuid.uuid4()), "1.1", "Design", 5),),
        constraints=Constraints(budget_total_cents=ctx.budget_cents),
    )
    sap = uuid.uuid4()
    conn = Conn(
        Rows(
            [
                {
                    "budget_total_cents": None,
                    "deadline_answer": "hard",
                    "specialist_answer": None,
                    "specialist_rate_confirmed": False,
                    "contingency_pct": Decimal("10.00"),
                }
            ]
        ),
        Rows([{"estimate_confirmed_at": None}, {"estimate_confirmed_at": NOW}]),
        Rows([{"type": "FS", "total": 4}, {"type": "SS", "total": 1}]),
        Rows([{"count": 2}]),
        Rows([{"skill_id": sap, "name": "SAP", "required": True}]),
        Rows([{"source_quote": "Budget is $400K"}, {"source_quote": "Budget approved at $375K"}]),
    )
    facts = await OptimizationRepository(conn).input_facts(ctx, inp)  # type: ignore[arg-type]
    assert (facts.confirmed_ai_estimates, facts.unconfirmed_estimates) == (1, 1)
    assert facts.dependency_types == {"FS": 4, "SS": 1}
    assert facts.team_from_project is True
    assert facts.skills[0].name == "SAP"
    assert facts.skills[0].covered is False
    assert facts.skill_gaps == ["SAP"]
    assert facts.budget_quote == "Budget approved at $375K"
    assert (facts.deadline_answer, facts.contingency_pct) == ("hard", 10.0)
    assert "wbs_nodes.origin = " in conn.sql[1]
    assert "ILIKE" in conn.sql[5]


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
    from app.optimization.model import Constraints, SkillRequirement, Task

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
