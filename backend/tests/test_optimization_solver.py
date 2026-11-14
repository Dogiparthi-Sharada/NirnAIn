"""Pure optimizer: calendar, baseline, CP-SAT model, infeasibility explanations."""

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
    out = solve(make_input(), FAST)
    assert out.status == "optimal"
    assert out.solver == SOLVER_NAME
    assert out.plan is not None
    a, b = out.plan.tasks["a"], out.plan.tasks["b"]
    assert (a.start, a.end, b.start, b.end) == (0, 5, 5, 15)
    # The owner is kept (cheaper and no change penalty).
    assert a.resource_id == b.resource_id == "e1"
    # 15 days x $200 + (40h + 80h) x $100.
    assert out.objective == 15 * 20_000 + 120 * 10_000
    assert out.gap == 0
    assert not out.hit_time_limit


def test_skill_requirement_forces_reassignment() -> None:
    inp = make_input(
        tasks=(
            Task("a", "1.1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.1.2", "Build", 10, owner_id="e1", skills=(SAP,)),
        )
    )
    out = solve(inp, FAST)
    assert out.status == "optimal"
    assert out.plan is not None
    assert out.plan.tasks["b"].resource_id == "e2"
    assert out.plan.tasks["a"].resource_id == "e1"


def test_capacity_serialises_full_time_tasks() -> None:
    inp = make_input(
        tasks=(
            Task("a", "1.1", "A", 5, owner_id="e1"),
            Task("b", "1.2", "B", 5, owner_id="e1"),
        ),
        employees=(Employee("e1", "Solo", hourly_rate_cents=1),),
        dependencies=(),
        constraints=Constraints(capacity_mode="hard"),
    )
    out = solve(inp, FAST)
    assert out.plan is not None
    assert out.plan.makespan == 10


def test_allocations_reduce_capacity() -> None:
    inp = make_input(
        tasks=(Task("a", "1.1", "A", 5, owner_id="e1"),),
        employees=(Employee("e1", "Busy", hourly_rate_cents=1),),
        dependencies=(),
        allocations=(Allocation("e1", MONDAY, 40),),
        constraints=Constraints(capacity_mode="hard"),
    )
    out = solve(inp, FAST)
    assert out.plan is not None
    assert out.plan.tasks["a"].start == 5  # first week is fully booked elsewhere


def test_hard_deadline_impossible_is_infeasible_with_iis() -> None:
    # Each task fits in 10 working days on its own; only the FS chain (15) doesn't.
    inp = with_constraints(make_input(), must_finish_by=date(2026, 10, 16))
    out = solve(inp, FAST)
    assert out.status == "infeasible"
    assert out.plan is None
    names = [c.name for c in out.conflicts]
    assert names == [
        "Deadline: must finish by Oct 16, 2026",
        "Dependency: 1.1.1 Design → 1.1.2 Build",
    ]
    assert out.conflicts[1].explanation == "Build can't start until Design finishes."
    deadline = next(c for c in out.conflicts if c.name.startswith("Deadline"))
    assert "Oct 23, 2026" in deadline.explanation


def test_soft_deadline_is_feasible_with_tardiness() -> None:
    inp = with_constraints(make_input(), must_finish_by=date(2026, 10, 9), deadline_mode="soft")
    out = solve(inp, FAST)
    assert out.status == "optimal"
    assert out.plan is not None
    assert out.plan.makespan == 15
    late_days = 15 - 5
    assert out.objective == 15 * 20_000 + 120 * 10_000 + late_days * 500 * 100


def test_hard_budget_cap_infeasible_names_budget() -> None:
    inp = with_constraints(make_input(), budget_total_cents=50_000)
    out = solve(inp, FAST)
    assert out.status == "infeasible"
    assert [c.name for c in out.conflicts] == ["Budget cap: $500"]
    assert "$12,000" in out.conflicts[0].explanation


def test_soft_budget_is_penalised_not_infeasible() -> None:
    inp = with_constraints(make_input(), budget_total_cents=50_000, budget_mode="soft")
    out = solve(inp, FAST)
    assert out.status == "optimal"


def test_labor_cap_and_skill_conflicts() -> None:
    inp = with_constraints(make_input(), labor_cap_cents=10)
    assert [c.name for c in solve(inp, FAST).conflicts] == ["Labor cap: $0"]

    inp = make_input(
        tasks=(Task("b", "1.2", "Build", 5, skills=(SAP,)),),
        employees=(Employee("e1", "Mark Chen"),),
        dependencies=(),
        constraints=Constraints(allow_contractors=False),
    )
    out = solve(inp, FAST)
    assert out.status == "infeasible"
    (conflict,) = out.conflicts
    assert conflict.name == "Skill: 1.2 Build requires SAP ≥ 4"
    assert "contractors are turned off" in conflict.explanation


def test_contractor_fills_skill_gap() -> None:
    inp = make_input(
        tasks=(Task("b", "1.2", "Build", 5, skills=(SAP,)),),
        employees=(Employee("e1", "Mark Chen"),),
        dependencies=(),
        constraints=Constraints(contractor_rate_cents=15_000),
    )
    out = solve(inp, FAST)
    assert out.plan is not None
    assert out.plan.tasks["b"].resource_id == "contractor:sap"


def test_soft_skill_mode_allows_unqualified() -> None:
    inp = make_input(
        tasks=(Task("b", "1.2", "Build", 5, skills=(SAP,)),),
        employees=(Employee("e1", "Mark Chen"),),
        dependencies=(),
        constraints=Constraints(skill_mode="soft", allow_contractors=False),
    )
    out = solve(inp, FAST)
    assert out.status == "optimal"


def test_milestones_hard_and_soft() -> None:
    hard = make_input(
        milestones=(Milestone("m", "Design done", date(2026, 10, 6), "hard", ("a",)),)
    )
    out = solve(hard, FAST)
    assert out.status == "infeasible"
    assert out.conflicts[0].name == "Milestone: Design done by Oct 6, 2026"
    soft = make_input(
        milestones=(Milestone("m", "Design done", date(2026, 10, 6), "soft", ("a",)),)
    )
    assert solve(soft, FAST).status == "optimal"


def test_hard_capacity_conflict_is_explained() -> None:
    inp = make_input(
        tasks=(
            Task("a", "1.1", "A", 5),
            Task("b", "1.2", "B", 5),
        ),
        employees=(Employee("e1", "Solo"),),
        dependencies=(),
        constraints=Constraints(capacity_mode="hard", must_finish_by=date(2026, 10, 9)),
    )
    out = solve(inp, FAST)
    assert out.status == "infeasible"
    names = {c.name for c in out.conflicts}
    assert names == {"Deadline: must finish by Oct 9, 2026", "Capacity: Solo (40h/week)"}
    deadline = next(c for c in out.conflicts if c.name.startswith("Deadline"))
    assert "capacity" in deadline.explanation


def test_time_limit_path() -> None:
    tasks = tuple(
        Task(f"t{i}", f"1.{i}", f"Task {i}", 3 + (i * 7) % 11, owner_id=f"e{i % 3}")
        for i in range(60)
    )
    deps = tuple(Dependency(f"t{i}", f"t{i + 7}") for i in range(0, 50, 3))
    team = tuple(
        Employee(f"e{i}", f"Person {i}", hourly_rate_cents=5000 + 700 * i) for i in range(8)
    )
    inp = make_input(tasks=tasks, employees=team, dependencies=deps)
    out = solve(inp, SolveOptions(time_limit_s=0.05, workers=1))
    assert out.status in ("feasible", "timeout")
    assert out.hit_time_limit
    if out.status == "timeout":
        assert out.message is not None
        assert "No feasible plan" in out.message
    else:
        assert out.plan is not None


def test_status_mapping() -> None:
    assert map_status(cp_model.OPTIMAL) == "optimal"
    assert map_status(cp_model.FEASIBLE) == "feasible"
    assert map_status(cp_model.INFEASIBLE) == "infeasible"
    assert map_status(cp_model.UNKNOWN) == "timeout"
    assert map_status(cp_model.MODEL_INVALID) == "failed"


def test_stopped_solve_returns_quickly() -> None:
    control = SolveControl()
    control.stop()
    assert control.stopped
    out = solve(make_input(), SolveOptions(time_limit_s=30), control)
    assert out.wall_seconds < 5
