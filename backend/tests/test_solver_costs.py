"""Tests of what people cost the plan (round-2 decision D1).

Purpose: Employees cost their company rate, else the standard $85/h; contractors their own rate,
else the project's contractor rate. Overtime adds 0.5x an employee's rate (1.5x in total) and
nothing for a contractor. A budget's reserve is held back from what the plan may spend.
How it fits: Pure tests of app/optimization/resources.py, the solver and the report; layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from app.optimization.explain import build_report
from app.optimization.model import (
    DEFAULT_HOURLY_RATE_CENTS,
    Constraints,
    Employee,
    PlanningInput,
    Task,
)
from app.optimization.resources import build_resources
from app.optimization.solver import solve
from tests.support.solver_inputs import FAST, make_input


def test_employees_and_contractors_are_priced_by_their_records() -> None:
    """Rates come from each person's record, else the standard or contractor rate."""
    planning_input = make_input(
        tasks=(
            Task("a", "1.1", "Design", 5, owner_id="mark"),
            Task("b", "1.2", "Build", 5, owner_id="dana"),
        ),
        employees=(
            Employee("mark", "Mark Chen"),  # no company rate on record
            Employee("dana", "Dana Lee", hourly_rate_cents=12_000, is_contractor=True),
            Employee("sam", "Sam Okafor", is_contractor=True),
        ),
        dependencies=(),
        constraints=Constraints(contractor_rate_cents=15_000, allow_contractors=False),
    )
    rates = {resource.id: resource.rate_cents for resource in build_resources(planning_input)}
    assert rates == {"mark": DEFAULT_HOURLY_RATE_CENTS, "dana": 12_000, "sam": 15_000}
    report = build_report(planning_input, solve(planning_input, FAST))
    assert report.expected_cost_cents == 40 * DEFAULT_HOURLY_RATE_CENTS + 40 * 12_000


@pytest.mark.parametrize(("is_contractor", "premium"), [(False, 10 * 5_000), (True, 0)])
def test_overtime_costs_half_again_for_employees_only(is_contractor: bool, premium: int) -> None:
    """Overtime adds half the rate for employees and nothing for contractors."""
    # A full-time 5-day package and a 2 h/day one, both due in 5 days: 10 h a day
    # for a 40 h/week person, so 2 h of overtime a day for 5 days.
    planning_input = make_input(
        tasks=(
            Task("a", "1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.2", "Review", 5, effort_hours=10, owner_id="e1"),
        ),
        employees=(Employee("e1", "Ana", hourly_rate_cents=10_000, is_contractor=is_contractor),),
        dependencies=(),
        constraints=Constraints(capacity_mode="soft", must_finish_by=date(2026, 10, 9)),
    )
    outcome = solve(planning_input, FAST)
    assert outcome.plan is not None
    report = build_report(planning_input, outcome)
    assert report.expected_cost_cents == 50 * 10_000 + premium


# --------------------------------------------------------------------------- reserve (R5)
def _thousand_dollar_package(**constraints: Any) -> PlanningInput:
    """Pack MICRO-COST-RESERVE: one 8 h package by a $125/h contractor, $1,000 of labor."""
    return make_input(
        tasks=(Task("t", "1.1", "Micro task", 1, owner_id="q"),),
        employees=(Employee("q", "Q", hourly_rate_cents=12_500, is_contractor=True),),
        dependencies=(),
        constraints=Constraints(allow_contractors=False, **constraints),
    )


def test_a_hard_budget_holds_back_its_reserve() -> None:
    """A hard budget's reserve is held back; the conflict says by how much the plan is short."""
    # $1,100 with a 10% reserve leaves $990 to spend: $10 short.
    short = _thousand_dollar_package(budget_total_cents=110_000, contingency_pct=10)
    outcome = solve(short, FAST)
    assert outcome.status == "infeasible"
    (conflict,) = outcome.conflicts
    assert conflict.name == "Budget cap: $1,100"
    assert conflict.explanation == (
        "Even the cheapest qualified staffing costs about $1,000, $10 more than "
        "the $990 left after holding back the $110 reserve."
    )
    # $1,112 holds back $111.20 and leaves $1,000.80: the plan fits.
    enough = _thousand_dollar_package(budget_total_cents=111_200, contingency_pct=10)
    assert solve(enough, FAST).status == "optimal"
    no_reserve = replace(short, constraints=replace(short.constraints, contingency_pct=0))
    assert solve(no_reserve, FAST).status == "optimal"


def test_a_soft_budget_is_penalized_from_the_reserve_on() -> None:
    """A soft budget is penalized from the reserve on, and the summary says how much was used."""
    planning_input = _thousand_dollar_package(
        budget_total_cents=110_000, contingency_pct=10, budget_mode="soft"
    )
    outcome = solve(planning_input, FAST)
    no_reserve = solve(
        replace(planning_input, constraints=replace(planning_input.constraints, contingency_pct=0)),
        FAST,
    )
    assert outcome.objective is not None
    assert no_reserve.objective is not None
    # $10 into the reserve costs one $1,000 unit of the budget penalty.
    assert outcome.objective - no_reserve.objective == 1_000 * 100
    summary = (build_report(planning_input, outcome).result or {})["summary"]
    assert summary["softViolations"] == ["$10 of the $110 reserve used"]
