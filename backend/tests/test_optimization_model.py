"""Tests of the optimizer's building blocks: calendar, formatting, baseline, load and snapshot.

Purpose: The working-day calendar, the money and week labels, the baseline plan, the load
arithmetic, the frozen input snapshot and the resource list must be right before any solve.
How it fits: Pure tests of app/optimization (calendar, formatting, baseline, load, model, resources)
on the inputs of tests/support/solver_inputs.py; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.optimization import formatting
from app.optimization.baseline import baseline_plan, dependency_offset
from app.optimization.calendar import (
    first_workday,
    week_index,
    week_workdays,
    workday_date,
    workdays_through,
)
from app.optimization.load import allocation_blocks, daily_capacity_tenths, task_daily_tenths
from app.optimization.model import (
    DEFAULT_HOURLY_RATE_CENTS,
    Allocation,
    Constraints,
    Dependency,
    Employee,
    Milestone,
    PlanningInput,
    SkillRequirement,
    SnapshotError,
    Task,
)
from app.optimization.resources import build_resources
from app.optimization.solver import (
    solve,
)
from tests.support.solver_inputs import FAST, MONDAY, SAP, make_input, with_constraints


# --------------------------------------------------------------------------- calendar / format
def test_calendar_working_days() -> None:
    """Working days skip weekends (unless six are worked) and count by week."""
    saturday = date(2026, 10, 10)
    assert first_workday(saturday, 5) == date(2026, 10, 12)
    assert first_workday(saturday, 6) == saturday
    assert workday_date(MONDAY, 0, 5) == MONDAY
    assert workday_date(MONDAY, 5, 5) == date(2026, 10, 12)
    assert workday_date(date(2026, 10, 7), 3, 5) == date(2026, 10, 12)  # Wed + 3 -> Mon
    assert workdays_through(MONDAY, date(2026, 10, 9), 5) == 5
    assert workdays_through(MONDAY, date(2026, 10, 11), 5) == 5
    assert workdays_through(MONDAY, date(2026, 10, 12), 5) == 6
    assert workdays_through(MONDAY, date(2026, 10, 1), 5) == 0
    assert week_index(MONDAY, date(2026, 10, 19)) == 2
    assert week_workdays(MONDAY, date(2026, 10, 12), 5) == (5, 10)
    assert week_workdays(MONDAY, date(2026, 9, 28), 5) == (0, 0)


def test_formatting() -> None:
    """Money, dates, week ranges, shifts and plurals read as the UI shows them."""
    assert formatting.money(36_120_000) == "$361,200"
    assert formatting.money(-150) == "-$2"
    assert formatting.day(date(2025, 10, 14)) == "Oct 14, 2025"
    assert formatting.weeks_label(2, 5) == f"Wk 3{formatting.EN_DASH}6"
    assert formatting.weeks_label(0, 0) == "Wk 1"
    assert formatting.shift(0, 5) == "—"
    assert formatting.shift(10, 5) == "+2w"
    assert formatting.shift(-3, 5) == "-3d"
    assert formatting.shift(7, 5) == "+1.4w"
    assert formatting.plural(1, "week") == "1 week"
    assert formatting.plural(3, "week") == "3 weeks"


def test_dependency_offsets() -> None:
    """Each dependency type offsets the successor from the predecessor as it should."""
    assert dependency_offset(Dependency("a", "b", "FS", 2), 5, 3) == 7
    assert dependency_offset(Dependency("a", "b", "SS", 1), 5, 3) == 1
    assert dependency_offset(Dependency("a", "b", "FF", 0), 5, 3) == 2
    assert dependency_offset(Dependency("a", "b", "SF", 0), 5, 3) == -3


def test_baseline_forward_pass_keeps_owners() -> None:
    """The baseline schedules as early as dependencies allow and keeps current owners."""
    planning_input = make_input()
    plan = baseline_plan(planning_input)
    assert (plan.tasks["a"].start, plan.tasks["b"].start) == (0, 5)
    assert plan.makespan == 15
    assert {scheduled.resource_id for scheduled in plan.tasks.values()} == {"e1"}
    gone = make_input(employees=(Employee("e2", "Lisa"),))
    assert baseline_plan(gone).tasks["a"].resource_id is None


def test_load_arithmetic() -> None:
    """Daily load and capacity are in tenths of hours; bookings are capped and clipped."""
    assert task_daily_tenths(Task("a", "1", "A", 10, effort_hours=40)) == 40
    assert task_daily_tenths(Task("a", "1", "A", 5)) == 80  # 8h/day default
    assert daily_capacity_tenths(40, 5) == 80
    planning_input = make_input(
        allocations=(
            Allocation("e1", date(2026, 10, 12), 100),  # capped at capacity
            Allocation("e1", date(2026, 9, 28), 10),  # before start: dropped
            Allocation("nobody", date(2026, 10, 12), 10),
        )
    )
    blocks = allocation_blocks(planning_input)
    assert list(blocks) == ["e1"]
    (block,) = blocks["e1"]
    assert (block.lo, block.hi, block.tenths) == (5, 10, 80)


# --------------------------------------------------------------------------- snapshot
def test_snapshot_round_trip() -> None:
    """An input survives being frozen into a snapshot and read back."""
    planning_input = make_input(
        tasks=(Task("a", "1.1", "A", 3, effort_hours=12.5, skills=(SAP,)),),
        milestones=(Milestone("m", "Go-live", date(2026, 11, 2), "soft", ("a",)),),
        allocations=(Allocation("e1", date(2026, 10, 12), 8),),
        constraints=Constraints(must_finish_by=date(2026, 12, 1), contractor_rate_cents=9000),
        deliverables={"1": "Discovery"},
    )
    assert PlanningInput.from_snapshot(planning_input.to_snapshot()) == planning_input


@pytest.mark.parametrize(
    "mutate",
    [
        lambda frozen: frozen.update(version=99),
        lambda frozen: frozen.pop("tasks"),
        lambda frozen: frozen["tasks"][0].update(duration_days=0),
        lambda frozen: frozen["constraints"].update(budget_mode="maybe"),
        lambda frozen: frozen["constraints"].update(working_days_per_week=9),
        lambda frozen: frozen["dependencies"][0].update(type="XX"),
        lambda frozen: frozen.update(start_date="not-a-date"),
    ],
)
def test_snapshot_rejects_bad_data(mutate: Any) -> None:
    """Snapshots with a bad version, fields or values are refused."""
    snapshot = make_input().to_snapshot()
    mutate(snapshot)
    with pytest.raises(SnapshotError):
        PlanningInput.from_snapshot(snapshot)


def test_snapshot_sanitises_penalties() -> None:
    """Negative or NaN penalties in a snapshot fall back to the defaults."""
    snapshot = make_input().to_snapshot()
    snapshot["constraints"].update(deadline_penalty_per_day=-5, budget_penalty=float("nan"))
    constraints = PlanningInput.from_snapshot(snapshot).constraints
    assert (constraints.deadline_penalty_per_day, constraints.budget_penalty) == (500.0, 1000.0)


# --------------------------------------------------------------------------- resources
def test_contractors_only_for_unstaffable_skills_unless_any_work() -> None:
    """Contractors are added only for skills nobody has, or generally when allowed."""
    task = Task("x", "1.2", "SAP Integration", 5, skills=(SkillRequirement("abap", "ABAP", 3),))
    planning_input = make_input(tasks=(task,))
    assert len(build_resources(planning_input)) == 2  # no contractor rate
    planning_input = with_constraints(planning_input, contractor_rate_cents=20_000)
    contractor = build_resources(planning_input)[-1]
    assert (contractor.id, contractor.name, contractor.kind) == (
        "contractor:abap",
        "Contractor: ABAP specialist",
        "contractor",
    )
    assert contractor.rate_cents == 20_000
    assert len(build_resources(with_constraints(planning_input, allow_contractors=False))) == 2
    # "Bring in a contractor" (D2) adds one generic contractor for the other work.
    generic = build_resources(with_constraints(planning_input, contractor_any_work=True))[-1]
    assert (generic.id, generic.name) == ("contractor", "Contractor")


def test_unknown_rate_uses_the_default_rate() -> None:
    """People without a rate cost the default rate, so a plan never costs $0."""
    planning_input = make_input(
        employees=(
            Employee("e1", "A", hourly_rate_cents=10_000),
            Employee("e2", "B", hourly_rate_cents=None),
        )
    )
    assert [resource.rate_cents for resource in build_resources(planning_input)] == [
        10_000,
        DEFAULT_HOURLY_RATE_CENTS,
    ]
    # Nobody priced: the plan still costs something (QA saw $0 for every team built in the app).
    unpriced = make_input(employees=(Employee("e1", "A"),))
    outcome = solve(unpriced, FAST)
    assert outcome.plan is not None
    assert outcome.objective == 15 * 20_000 + 120 * DEFAULT_HOURLY_RATE_CENTS
