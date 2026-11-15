"""Tests of loading the optimizer's input and the confirmation sheet's facts from the database.

Purpose: The frozen input must have exactly the project's work packages, team, skills, dependencies,
constraints, milestones and bookings, with the step-5 answers deciding the deadline mode and whether
specialists may be used.
How it fits: Compiles the loader of app/repositories/optimization.py and constraint_input.py against
the recording connection of tests/support/connections.py; layer: tests.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

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
from tests.support.connections import Conn, Rows, project_context, wbs_node_row
from tests.support.solver_inputs import make_input

# When a person confirmed an AI estimate in the fake rows.
CONFIRMED_AT = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


async def test_load_input_builds_planning_input() -> None:
    """Work packages, team, skills, dependencies (expanded), milestones and bookings are loaded."""
    project = project_context()
    root, first_deliverable, first_package, empty_package, second_deliverable = (
        uuid.uuid4() for _ in range(5)
    )
    mark, lisa, sap = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    conn = Conn(
        Rows(
            [
                wbs_node_row(root, None, 1, "1"),
                wbs_node_row(first_deliverable, root, 2, "1.1"),
                wbs_node_row(
                    first_package,
                    first_deliverable,
                    3,
                    "1.1.1",
                    duration_days=5,
                    effort_hours=Decimal("30.0"),
                    owner_employee_id=mark,
                ),
                wbs_node_row(
                    empty_package, first_deliverable, 3, "1.1.2", duration_days=0
                ),  # no duration: not a work package
                wbs_node_row(
                    second_deliverable, root, 2, "1.2", duration_days=10
                ),  # childless level 2: work package
            ]
        ),
        Rows([]),  # no applied plan scheduled the 0-day package: it isn't finished work
        Rows(
            [{"wbs_node_id": first_package, "skill_id": sap, "name": "SAP", "min_proficiency": 3}]
        ),
        Rows([{"skill_id": sap, "name": "SAP", "required": True}]),  # the project's list
        Rows(
            [
                {
                    "id": mark,
                    "full_name": "Mark",
                    "hours_per_week": Decimal("40.0"),
                    "hourly_rate_cents": 9000,
                    "is_contractor": False,
                },
                {
                    "id": lisa,
                    "full_name": "Lisa",
                    "hours_per_week": Decimal("16.0"),  # hours on this project
                    "hourly_rate_cents": None,
                    "is_contractor": True,
                },
            ]
        ),
        Rows([{"employee_id": mark, "skill_id": sap, "proficiency": 4}]),
        # dependency on the summary node d1 expands to its work package wp1
        Rows(
            [
                {
                    "predecessor_id": first_deliverable,
                    "successor_id": second_deliverable,
                    "type": "FS",
                    "lag_days": 2,
                }
            ]
        ),
        Rows([]),  # no constraint set -> project fallbacks
        Rows(
            [
                {
                    "id": uuid.uuid4(),
                    "name": "Phase 1",
                    "due_date": date(2026, 11, 2),
                    "wbs_node_id": first_deliverable,
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
        Rows([{"employee_id": lisa, "week_start": date(2026, 10, 12), "hours": Decimal("16.0")}]),
    )
    planning_input = await OptimizationRepository(conn).load_input(project, date(2026, 10, 1))  # type: ignore[arg-type]
    assert [task.code for task in planning_input.tasks] == ["1.1.1", "1.2"]
    first_task = planning_input.tasks[0]
    assert (first_task.effort_hours, first_task.owner_id) == (30.0, str(mark))
    assert first_task.skills[0].skill_name == "SAP"
    assert [person.name for person in planning_input.employees] == ["Mark", "Lisa"]
    assert [employee.weekly_capacity_hours for employee in planning_input.employees] == [40.0, 16.0]
    assert "project_skills" in conn.sql[3]
    assert "project_team_members" in conn.sql[4]  # the project's team, not the whole org
    assert planning_input.deliverables == {"1.1": "Node 1.1", "1.2": "Node 1.2"}
    assert planning_input.constraints.allow_contractors is False  # nothing answered yet
    assert planning_input.employees[0].skills == {str(sap): 4}
    assert planning_input.employees[1].is_contractor
    assert [
        (dependency.predecessor_id, dependency.successor_id, dependency.lag_days)
        for dependency in planning_input.dependencies
    ] == [(str(first_package), str(second_deliverable), 2)]
    assert planning_input.start_date == date(2026, 10, 5)
    assert planning_input.constraints.budget_total_cents == 37_500_000
    assert planning_input.constraints.must_finish_by == date(2027, 1, 29)
    assert [milestone.name for milestone in planning_input.milestones] == [
        "Phase 1"
    ]  # nodeless milestone is informational
    assert planning_input.milestones[0].task_ids == (str(first_package),)
    assert planning_input.allocations[0].hours == 16.0
    assert "wbs_nodes_with_code" in conn.sql[0]
    assert "allocations.project_id != " in conn.sql[-1]
    # The snapshot survives a round trip.
    assert PlanningInput.from_snapshot(planning_input.to_snapshot()) == planning_input


async def test_load_input_with_constraint_set_and_no_data() -> None:
    """A saved constraint set is used, with project fallbacks and the whole organization as team."""
    constraint_row = {
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
    conn = Conn(Rows([]), Rows([]), Rows([]), Rows([]), Rows([constraint_row]), Rows([]))
    project = project_context()
    planning_input = await OptimizationRepository(conn).load_input(project, date(2026, 10, 1))  # type: ignore[arg-type]
    assert (planning_input.tasks, planning_input.employees) == ((), ())
    constraints = planning_input.constraints
    assert planning_input.start_date == date(2026, 11, 2)
    assert constraints.budget_total_cents == project.budget_cents
    assert constraints.must_finish_by == project.target_end_date
    assert (
        constraints.labor_cap_cents,
        constraints.budget_mode,
        constraints.working_days_per_week,
    ) == (100, "soft", 6)
    assert (constraints.capacity_mode, constraints.skill_mode, constraints.deadline_mode) == (
        "hard",
        "soft",
        "soft",
    )
    assert constraints.contingency_pct == 12.5
    assert (constraints.allow_contractors, constraints.contractor_rate_cents) == (
        True,
        17_500,
    )  # default rate
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
    """The deadline answer decides its mode; only "yes" allows specialists."""
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
    """The facts read answers, confirmations, dependency types, skills and the budget quote."""
    project = project_context()
    planning_input = make_input(
        tasks=(Task(str(uuid.uuid4()), "1.1", "Design", 5),),
        constraints=Constraints(budget_total_cents=project.budget_cents),
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
    facts = await OptimizationRepository(conn).input_facts(project, planning_input)  # type: ignore[arg-type]
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
