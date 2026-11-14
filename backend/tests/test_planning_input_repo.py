"""Moved test support."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.optimization.model import Constraints, PlanningInput, Task
from app.repositories.constraint_input import build_constraints
from app.repositories.optimization import (
    OptimizationRepository,
)
from tests.support.connections import Conn, Rows, project_context
from tests.support.solver_inputs import make_input

# When a person confirmed an AI estimate in the fake rows.
CONFIRMED_AT = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


async def test_load_input_builds_planning_input() -> None:
    ctx = project_context()
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
            "description": None,
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
        Rows([]),  # no applied plan scheduled the 0-day package: it isn't finished work
        Rows([{"wbs_node_id": wp1, "skill_id": sap, "name": "SAP", "min_proficiency": 3}]),
        Rows([{"skill_id": sap, "name": "SAP", "required": True}]),  # the project's list
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
    assert "project_skills" in conn.sql[3]
    assert "project_team_members" in conn.sql[4]  # the project's team, not the whole org
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
        "contingency_pct": Decimal("12.50"),
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
    ctx = project_context()
    inp = await OptimizationRepository(conn).load_input(ctx, date(2026, 10, 1))  # type: ignore[arg-type]
    assert (inp.tasks, inp.employees) == ((), ())
    c = inp.constraints
    assert inp.start_date == date(2026, 11, 2)
    assert c.budget_total_cents == ctx.budget_cents
    assert c.must_finish_by == ctx.target_end_date
    assert (c.labor_cap_cents, c.budget_mode, c.working_days_per_week) == (100, "soft", 6)
    assert (c.capacity_mode, c.skill_mode, c.deadline_mode) == ("hard", "soft", "soft")
    assert c.contingency_pct == 12.5
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
        contingency_pct=0,
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
    constraints, _ = build_constraints(constraint_set, project_context(), date(2026, 10, 1))
    assert constraints.deadline_mode == deadline_mode
    assert constraints.allow_contractors is contractors
    assert constraints.contractor_rate_cents == 9_000
    assert (constraints.budget_mode, constraints.capacity_mode) == ("soft", "hard")


async def test_input_facts_reads_answers_and_confirmations() -> None:
    ctx = project_context()
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
        Rows([{"estimate_confirmed_at": None}, {"estimate_confirmed_at": CONFIRMED_AT}]),
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
