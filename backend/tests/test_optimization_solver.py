"""Tests of the pure optimizer: CP-SAT schedules, hard and soft limits, and conflict explanations.

Purpose: Prove the solver finds the known optimum, respects skills, capacity, bookings, deadlines,
budgets and milestones (hard ones as limits, soft ones as penalties), and names the conflicting
constraints in plain words when no plan exists.
How it fits: Pure tests of app/optimization/solver.py on the small inputs of
tests/support/solver_inputs.py; layer: tests.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date

from ortools.sat.python import cp_model

from app.optimization.model import (
    Allocation,
    Constraints,
    Dependency,
    Employee,
    Milestone,
    Task,
)
from app.optimization.solver import (
    SOLVER_NAME,
    SolveControl,
    SolveOptions,
    map_status,
    solve,
)
from tests.support.solver_inputs import FAST, MONDAY, SAP, make_input, with_constraints


# --------------------------------------------------------------------------- solving
def test_tiny_instance_known_optimum() -> None:
    """The two-task chain is solved optimally with its owner kept, at the known cost."""
    outcome = solve(make_input(), FAST)
    assert outcome.status == "optimal"
    assert outcome.solver == SOLVER_NAME
    assert outcome.plan is not None
    task_a, task_b = outcome.plan.tasks["a"], outcome.plan.tasks["b"]
    assert (task_a.start, task_a.end, task_b.start, task_b.end) == (0, 5, 5, 15)
    # The owner is kept (cheaper and no change penalty).
    assert task_a.resource_id == task_b.resource_id == "e1"
    # 15 days x $200 + (40h + 80h) x $100.
    assert outcome.objective == 15 * 20_000 + 120 * 10_000
    assert outcome.gap == 0
    assert not outcome.hit_time_limit


def test_skill_requirement_forces_reassignment() -> None:
    """A task needing a skill its owner lacks goes to someone who has it."""
    planning_input = make_input(
        tasks=(
            Task("a", "1.1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.1.2", "Build", 10, owner_id="e1", skills=(SAP,)),
        )
    )
    outcome = solve(planning_input, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    assert outcome.plan.tasks["b"].resource_id == "e2"
    assert outcome.plan.tasks["a"].resource_id == "e1"


def test_capacity_serialises_full_time_tasks() -> None:
    """With hard capacity one full-time person does two tasks one after the other."""
    planning_input = make_input(
        tasks=(
            Task("a", "1.1", "A", 5, owner_id="e1"),
            Task("b", "1.2", "B", 5, owner_id="e1"),
        ),
        employees=(Employee("e1", "Solo", hourly_rate_cents=1),),
        dependencies=(),
        constraints=Constraints(capacity_mode="hard"),
    )
    outcome = solve(planning_input, FAST)
    assert outcome.plan is not None
    assert outcome.plan.makespan == 10


def test_allocations_reduce_capacity() -> None:
    """Hours booked on other projects push the work past the booked week."""
    planning_input = make_input(
        tasks=(Task("a", "1.1", "A", 5, owner_id="e1"),),
        employees=(Employee("e1", "Busy", hourly_rate_cents=1),),
        dependencies=(),
        allocations=(Allocation("e1", MONDAY, 40),),
        constraints=Constraints(capacity_mode="hard"),
    )
    outcome = solve(planning_input, FAST)
    assert outcome.plan is not None
    assert outcome.plan.tasks["a"].start == 5  # first week is fully booked elsewhere


def test_hard_deadline_impossible_is_infeasible_with_iis() -> None:
    """An impossible hard deadline is infeasible and names the deadline and the dependency."""
    # Each task fits in 10 working days on its own; only the FS chain (15) doesn't.
    planning_input = with_constraints(make_input(), must_finish_by=date(2026, 10, 16))
    outcome = solve(planning_input, FAST)
    assert outcome.status == "infeasible"
    assert outcome.plan is None
    names = [conflict.name for conflict in outcome.conflicts]
    assert names == [
        "Deadline: must finish by Oct 16, 2026",
        "Dependency: 1.1.1 Design → 1.1.2 Build",
    ]
    assert outcome.conflicts[1].explanation == "Build can't start until Design finishes."
    deadline = next(
        conflict for conflict in outcome.conflicts if conflict.name.startswith("Deadline")
    )
    assert "Oct 23, 2026" in deadline.explanation


def test_soft_deadline_is_feasible_with_tardiness() -> None:
    """A missed soft deadline is allowed, at a penalty per late day."""
    planning_input = with_constraints(
        make_input(), must_finish_by=date(2026, 10, 9), deadline_mode="soft"
    )
    outcome = solve(planning_input, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    assert outcome.plan.makespan == 15
    late_days = 15 - 5
    assert outcome.objective == 15 * 20_000 + 120 * 10_000 + late_days * 500 * 100


def test_hard_budget_cap_infeasible_names_budget() -> None:
    """An impossible hard budget is infeasible and names the budget with the plan's cost."""
    planning_input = with_constraints(make_input(), budget_total_cents=50_000)
    outcome = solve(planning_input, FAST)
    assert outcome.status == "infeasible"
    assert [conflict.name for conflict in outcome.conflicts] == ["Budget cap: $500"]
    assert "$12,000" in outcome.conflicts[0].explanation


def test_soft_budget_is_penalised_not_infeasible() -> None:
    """An exceeded soft budget is allowed."""
    planning_input = with_constraints(make_input(), budget_total_cents=50_000, budget_mode="soft")
    outcome = solve(planning_input, FAST)
    assert outcome.status == "optimal"


def test_labor_cap_and_skill_conflicts() -> None:
    """An impossible labor cap or a skill nobody has (contractors off) are named."""
    planning_input = with_constraints(make_input(), labor_cap_cents=10)
    assert [named.name for named in solve(planning_input, FAST).conflicts] == ["Labor cap: $0"]

    planning_input = make_input(
        tasks=(Task("b", "1.2", "Build", 5, skills=(SAP,)),),
        employees=(Employee("e1", "Mark Chen"),),
        dependencies=(),
        constraints=Constraints(allow_contractors=False),
    )
    outcome = solve(planning_input, FAST)
    assert outcome.status == "infeasible"
    (conflict,) = outcome.conflicts
    assert conflict.name == "Skill: 1.2 Build requires SAP ≥ 4"
    assert "contractors are turned off" in conflict.explanation


def test_contractor_fills_skill_gap() -> None:
    """With a contractor rate a contractor takes the work nobody can do."""
    planning_input = make_input(
        tasks=(Task("b", "1.2", "Build", 5, skills=(SAP,)),),
        employees=(Employee("e1", "Mark Chen"),),
        dependencies=(),
        constraints=Constraints(contractor_rate_cents=15_000),
    )
    outcome = solve(planning_input, FAST)
    assert outcome.plan is not None
    assert outcome.plan.tasks["b"].resource_id == "contractor:sap"


def test_soft_skill_mode_allows_unqualified() -> None:
    """With soft skills an unqualified person may do the work."""
    planning_input = make_input(
        tasks=(Task("b", "1.2", "Build", 5, skills=(SAP,)),),
        employees=(Employee("e1", "Mark Chen"),),
        dependencies=(),
        constraints=Constraints(skill_mode="soft", allow_contractors=False),
    )
    outcome = solve(planning_input, FAST)
    assert outcome.status == "optimal"


def test_milestones_hard_and_soft() -> None:
    """A missed hard milestone is infeasible and named; a soft one is allowed."""
    hard = make_input(
        milestones=(Milestone("m", "Design done", date(2026, 10, 6), "hard", ("a",)),)
    )
    outcome = solve(hard, FAST)
    assert outcome.status == "infeasible"
    assert outcome.conflicts[0].name == "Milestone: Design done by Oct 6, 2026"
    soft = make_input(
        milestones=(Milestone("m", "Design done", date(2026, 10, 6), "soft", ("a",)),)
    )
    assert solve(soft, FAST).status == "optimal"


def test_hard_capacity_conflict_is_explained() -> None:
    """Hard capacity against a deadline names both, saying capacity is the cause."""
    planning_input = make_input(
        tasks=(
            Task("a", "1.1", "A", 5),
            Task("b", "1.2", "B", 5),
        ),
        employees=(Employee("e1", "Solo"),),
        dependencies=(),
        constraints=Constraints(capacity_mode="hard", must_finish_by=date(2026, 10, 9)),
    )
    outcome = solve(planning_input, FAST)
    assert outcome.status == "infeasible"
    names = {conflict.name for conflict in outcome.conflicts}
    assert names == {"Deadline: must finish by Oct 9, 2026", "Capacity: Solo (40h/week)"}
    deadline = next(
        conflict for conflict in outcome.conflicts if conflict.name.startswith("Deadline")
    )
    assert "capacity" in deadline.explanation


def test_time_limit_path() -> None:
    """A time limit too short for a large input gives a feasible plan or a timeout message."""
    tasks = tuple(
        Task(
            f"t{index}",
            f"1.{index}",
            f"Task {index}",
            3 + (index * 7) % 11,
            owner_id=f"e{index % 3}",
        )
        for index in range(60)
    )
    links = tuple(Dependency(f"t{index}", f"t{index + 7}") for index in range(0, 50, 3))
    team = tuple(
        Employee(f"e{index}", f"Person {index}", hourly_rate_cents=5000 + 700 * index)
        for index in range(8)
    )
    planning_input = make_input(tasks=tasks, employees=team, dependencies=links)
    outcome = solve(planning_input, SolveOptions(time_limit_s=0.05, workers=1))
    assert outcome.status in ("feasible", "timeout")
    assert outcome.hit_time_limit
    if outcome.status == "timeout":
        assert outcome.message is not None
        assert "No feasible plan" in outcome.message
    else:
        assert outcome.plan is not None


def test_status_mapping() -> None:
    """CP-SAT statuses map to the run statuses."""
    assert map_status(cp_model.OPTIMAL) == "optimal"
    assert map_status(cp_model.FEASIBLE) == "feasible"
    assert map_status(cp_model.INFEASIBLE) == "infeasible"
    assert map_status(cp_model.UNKNOWN) == "timeout"
    assert map_status(cp_model.MODEL_INVALID) == "failed"


def test_stopped_solve_returns_quickly() -> None:
    """A solve stopped before it starts returns at once."""
    control = SolveControl()
    control.stop()
    assert control.stopped
    outcome = solve(make_input(), SolveOptions(time_limit_s=30), control)
    assert outcome.wall_seconds < 5
