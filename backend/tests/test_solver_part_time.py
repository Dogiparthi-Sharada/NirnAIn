"""Part-time people stretch the plan; full-time hours shrink it back (QA's core scenario).

The PM cuts or raises someone's hours mid-project (`hoursPerWeek` overrides,
as Changes & Versions sends them) and the plan must react: a task's
duration follows whoever does it (`load.task_mode`), soft capacity no
longer prefers overbooking a person, and a part-timer who makes a hard date
impossible is named in the explanation and gets a priced remedy.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.optimization.explain import build_report
from app.optimization.load import Mode, task_mode
from app.optimization.model import (
    Constraints,
    Dependency,
    Employee,
    PlanningInput,
    SkillRequirement,
    Task,
)
from app.optimization.overrides import apply_overrides
from app.optimization.remedies import price_remedies
from app.optimization.solver import solve
from tests.test_optimization_solver import FAST, make_input

DATA = SkillRequirement("data", "Data Engineering", 3)


def _leo(hours: float = 40) -> Employee:
    return Employee(
        "leo",
        "Leo Martins",
        weekly_capacity_hours=hours,
        hourly_rate_cents=9_000,
        skills={"data": 4},
    )


def _pipeline() -> PlanningInput:
    """Leo is the only data engineer: Ingest (10 d, 80 h) then Model (5 d, 40 h)."""
    return make_input(
        tasks=(
            Task("a", "1.1.1", "Ingest", 10, effort_hours=80, owner_id="leo", skills=(DATA,)),
            Task("b", "1.1.2", "Model", 5, effort_hours=40, owner_id="leo", skills=(DATA,)),
            Task("c", "1.1.3", "Docs", 5, owner_id="ana"),
        ),
        employees=(_leo(), Employee("ana", "Ana Souza", hourly_rate_cents=8_000)),
        dependencies=(Dependency("a", "b"),),
        constraints=Constraints(must_finish_by=date(2027, 3, 31)),
    )


def _plan_at(inp: PlanningInput, hours: float) -> dict[str, Any]:
    """Re-plan with Leo at `hours` per week, as a change's override does."""
    changed = apply_overrides(inp, {"hoursPerWeek": {"leo": hours}})
    outcome = solve(changed, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    report = build_report(changed, outcome)
    assert report.result is not None
    days = {task_id: task.end - task.start for task_id, task in outcome.plan.tasks.items()}
    return {
        "makespan": outcome.plan.makespan,
        "days": days,
        "finish": report.completion_date,
        "summary": report.result["summary"],
        "report": report,
    }


def test_task_duration_follows_the_persons_hours() -> None:
    task = Task("a", "1", "A", 10, effort_hours=80)  # 8 h a day at the WBS duration
    assert task_mode(task, 80) == Mode(10, 80, stretched=False)  # 40 h/week: fits
    assert task_mode(task, 32) == Mode(25, 32, stretched=True)  # 16 h/week: 2.5x as long
    assert task_mode(task, None) == Mode(10, 80, stretched=False)  # specialist profile
    assert task_mode(task, 0) == Mode(10, 80, stretched=False)  # no hours: nothing to stretch


def test_cutting_hours_lengthens_the_plan_and_raising_them_shortens_it() -> None:
    inp = _pipeline()
    full = _plan_at(inp, 40)
    assert (full["makespan"], full["finish"]) == (15, date(2026, 10, 23))
    assert (full["days"]["a"], full["days"]["b"]) == (10, 5)

    cut = _plan_at(inp, 16)
    # 16 h/week is 3.2 h a day: Ingest 80 h -> 25 days, Model 40 h -> 13 days.
    assert (cut["days"]["a"], cut["days"]["b"]) == (25, 13)
    assert cut["days"]["c"] == 5  # Ana's work is untouched
    assert 2.4 <= cut["makespan"] / full["makespan"] <= 2.6
    assert cut["finish"] == date(2026, 11, 25)
    assert cut["summary"]["peakUtilization"]["percent"] <= 100
    assert not any("above their hours" in text for text in cut["summary"]["softViolations"])

    report = cut["report"]
    ingest = report.result["scheduleChanges"][0]
    assert ingest["reason"] == "Leo Martins at 16h/week: 25 working days instead of 10"
    assert {row.wbs_node_id: row.hours_per_week for row in report.assignments}["a"] == 16.0
    assert ("schedule_change", "a") in {
        (rec.type, rec.wbs_node_id) for rec in report.recommendations
    }

    # Giving hours back shortens the plan again, step by step.
    partial = _plan_at(inp, 32)
    assert (partial["days"]["a"], partial["days"]["b"]) == (13, 7)
    assert cut["makespan"] > partial["makespan"] > full["makespan"]
    assert _plan_at(inp, 40)["finish"] == full["finish"]


def _ten_hour_week(deadline: date, capacity_mode: str) -> PlanningInput:
    """Sam (10 h/week = 2 h a day) alone on a 5-day, 40-hour package."""
    return make_input(
        tasks=(Task("a", "1.1", "Build", 5, effort_hours=40, owner_id="sam"),),
        employees=(Employee("sam", "Sam Okafor", weekly_capacity_hours=10),),
        dependencies=(),
        constraints=Constraints(
            must_finish_by=deadline, deadline_mode="hard", capacity_mode=capacity_mode
        ),
    )


@pytest.mark.parametrize(
    ("capacity_mode", "moved_to", "title"),
    [
        ("hard", date(2026, 10, 30), "Move go-live to Oct 30 (+14 days)"),
        # Can flex: Sam may work 25% over (12.5 h/week), so 16 days instead of 20.
        ("soft", date(2026, 10, 26), "Move go-live to Oct 26 (+10 days)"),
    ],
)
def test_ten_hour_week_is_feasible_far_out_and_priced_when_tight(
    capacity_mode: str, moved_to: date, title: str
) -> None:
    far = solve(_ten_hour_week(date(2026, 12, 31), capacity_mode), FAST)
    assert far.status == "optimal"
    assert far.plan is not None
    assert far.plan.tasks["a"].end == 20  # 40 h at 2 h a day

    tight = _ten_hour_week(date(2026, 10, 16), capacity_mode)
    out = solve(tight, FAST)
    assert out.status == "infeasible"
    assert [conflict.name for conflict in out.conflicts] == [
        "Deadline: must finish by Oct 16, 2026",
        "Capacity: Sam Okafor (10h/week)",
    ]
    assert out.conflicts[1].explanation == (
        "Sam Okafor can't take on the work they're needed for within 10h/week. "
        "At those hours 1.1 Build takes 20 working days instead of 5."
    )
    remedy = {remedy.id: remedy for remedy in price_remedies(tight, FAST)}["move_deadline"]
    assert (remedy.completion_date, remedy.title) == (moved_to, title)


def test_soft_capacity_reassigns_instead_of_overbooking() -> None:
    # QA: Leo cut to 16 h/week kept both packages and showed 188-288% peaks.
    inp = make_input(
        tasks=(
            Task("a", "1.1", "Ingest", 10, effort_hours=80, owner_id="leo", skills=(DATA,)),
            Task("b", "1.2", "Transform", 10, effort_hours=80, owner_id="leo", skills=(DATA,)),
        ),
        employees=(
            _leo(16),
            Employee("ana", "Ana Souza", hourly_rate_cents=9_000, skills={"data": 3}),
        ),
        dependencies=(),
        constraints=Constraints(must_finish_by=date(2027, 3, 31), capacity_mode="soft"),
    )
    report = build_report(inp, solve(inp, FAST))
    assert report.result is not None
    summary = report.result["summary"]
    assert summary["softViolations"] == []
    assert summary["peakUtilization"]["percent"] <= 100
    assert summary["daysLate"] == 0


def test_soft_capacity_stretches_and_serialises_instead_of_overbooking() -> None:
    # Only Leo can do either package: two stretched 25-day packages back to back
    # beat running them side by side at twice his hours.
    inp = make_input(
        tasks=(
            Task("a", "1.1", "Ingest", 10, effort_hours=80, owner_id="leo", skills=(DATA,)),
            Task("b", "1.2", "Transform", 10, effort_hours=80, owner_id="leo", skills=(DATA,)),
        ),
        employees=(_leo(16),),
        dependencies=(),
        constraints=Constraints(must_finish_by=date(2027, 3, 31), capacity_mode="soft"),
    )
    outcome = solve(inp, FAST)
    assert outcome.plan is not None
    assert outcome.plan.makespan == 50
    report = build_report(inp, outcome)
    assert report.result is not None
    assert report.result["summary"]["softViolations"] == []
    assert report.result["utilization"] == [{"name": "Leo Martins", "before": 500, "after": 100}]


def test_hard_capacity_names_the_part_timer_who_cannot_fit() -> None:
    # Each package fits Sam's 20 h/week stretched to 10 days, but not both by Oct 23.
    inp = make_input(
        tasks=(
            Task("a", "1.1", "Build", 5, effort_hours=40, owner_id="sam"),
            Task("b", "1.2", "Test", 5, effort_hours=40, owner_id="sam"),
        ),
        employees=(Employee("sam", "Sam Okafor", weekly_capacity_hours=20),),
        dependencies=(),
        constraints=Constraints(capacity_mode="hard", must_finish_by=date(2026, 10, 23)),
    )
    out = solve(inp, FAST)
    assert out.status == "infeasible"
    assert [conflict.name for conflict in out.conflicts] == [
        "Deadline: must finish by Oct 23, 2026",
        "Capacity: Sam Okafor (20h/week)",
    ]
    assert "takes 10 working days instead of 5" in out.conflicts[1].explanation
