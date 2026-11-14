"""When a change moves the date: the target, the slip and the ways to keep it (round-2 D2).

A step-7 re-plan tries to keep the completion date of the plan in force
(`target_date`); other runs, the deadline. A plan that finishes later
reports `slipDays` and gets priced options: bring in a contractor, let
people work up to 25% overtime, or accept the new date. "Can flex"
capacity never goes beyond 25% over contracted hours.
"""

from __future__ import annotations

import dataclasses
from datetime import date

from app.optimization.explain import build_report
from app.optimization.model import Constraints, Employee, PlanningInput, Task
from app.optimization.overrides import apply_overrides, with_plan_in_force
from app.optimization.remedies import apply_remedy, price_remedies
from app.optimization.solver import solve
from tests.test_optimization_solver import FAST, make_input
from tests.test_solver_part_time import _pipeline


def _leo_cut_to_16_hours() -> PlanningInput:
    """Step 7: Leo goes from 40 to 16 h/week; the plan in force finished Oct 23."""
    in_force = with_plan_in_force(_pipeline(), {}, date(2026, 10, 23))
    return apply_overrides(in_force, {"hoursPerWeek": {"leo": 16}})


def test_a_late_replan_reports_its_target_and_slip() -> None:
    inp = _leo_cut_to_16_hours()
    report = build_report(inp, solve(inp, FAST))
    assert report.result is not None
    summary = report.result["summary"]
    assert (summary["completionDate"], summary["targetDate"], summary["slipDays"]) == (
        "2026-11-25",
        "2026-10-23",
        23,
    )
    on_time = build_report(_pipeline(), solve(_pipeline(), FAST)).result
    assert on_time is not None
    # Without a plan in force the target is the deadline (Mar 31, 2027): on time.
    assert (on_time["summary"]["targetDate"], on_time["summary"]["slipDays"]) == ("2027-03-31", 0)


def test_ways_to_keep_the_date_are_priced_against_this_plan() -> None:
    inp = _leo_cut_to_16_hours()
    current = solve(inp, FAST)
    remedies = {remedy.id: remedy for remedy in price_remedies(inp, FAST, current=current)}
    assert list(remedies) == ["specialist", "overtime", "move_deadline"]

    contractor = remedies["specialist"]
    # Only a contractor taking all of Leo's work keeps Oct 23: 120 h at the $175/h
    # estimate instead of Leo's $90/h.
    assert (contractor.title, contractor.completion_date, contractor.cost_cents) == (
        "Bring in a contractor",
        date(2026, 10, 23),
        120 * (17_500 - 9_000),
    )
    assert contractor.detail == (
        "A contractor takes 120 hours of the work at $175/h (estimate)"
        " · go-live Oct 23, 2026 (on the Oct 23 target)"
    )

    overtime = remedies["overtime"]
    assert overtime.title == "Overtime: Leo Martins up to 25% over their hours"
    # 20 h/week instead of 16: Ingest 20 days, Model 10, so Nov 13 instead of Nov 25.
    assert overtime.completion_date == date(2026, 11, 13)
    # Leo works 0.8 h a day over his hours for 30 days: 24 h at half his rate again.
    assert overtime.cost_cents == 24 * 4_500
    assert overtime.detail.startswith("24 overtime hours (1.5x for employees)")

    accept = remedies["move_deadline"]
    assert (accept.title, accept.cost_cents, accept.completion_date) == (
        "Accept Nov 25",
        0,
        date(2026, 11, 25),
    )
    assert accept.detail == (
        "Keep this plan: go-live Nov 25, 2026, 23 working days after Oct 23, 2026 · no extra cost"
    )

    # "Use this option" re-runs with the remedy: each run reaches the promised date,
    # at the promised extra cost.
    plan_cost = build_report(inp, current).expected_cost_cents
    for remedy in remedies.values():
        chosen = apply_remedy(inp, remedy.id)
        rerun = build_report(chosen, solve(chosen, FAST))
        assert rerun.completion_date == remedy.completion_date, remedy.id
        assert rerun.expected_cost_cents == plan_cost + (remedy.cost_cents or 0), remedy.id


def test_can_flex_stops_at_25_percent_over_contracted_hours() -> None:
    # Two full-time 5-day packages for one person by Oct 9: 200% of their hours.
    inp = make_input(
        tasks=(
            Task("a", "1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.2", "Build", 5, owner_id="e1"),
        ),
        employees=(Employee("e1", "Solo", hourly_rate_cents=10_000),),
        dependencies=(),
        constraints=Constraints(capacity_mode="soft", must_finish_by=date(2026, 10, 9)),
    )
    out = solve(inp, FAST)
    assert out.status == "infeasible"
    assert [conflict.name for conflict in out.conflicts] == [
        "Deadline: must finish by Oct 9, 2026",
        "Capacity: Solo (40h/week)",
    ]
    # Ten days are enough: overtime isn't needed, so none is used.
    relaxed = dataclasses.replace(
        inp, constraints=dataclasses.replace(inp.constraints, must_finish_by=date(2026, 10, 16))
    )
    report = build_report(relaxed, solve(relaxed, FAST))
    assert report.expected_cost_cents == 80 * 10_000
