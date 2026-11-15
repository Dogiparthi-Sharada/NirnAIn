"""Tests of the run report: KPIs, reassignments, schedule changes, utilization and rows.

Purpose: The plan must be explained the way the approved mockup shows it: budget and date KPIs, who
takes over which work and why, what moved and why, utilization before and after, and the rows stored
for the plan.
How it fits: Pure tests of app/optimization/explain.py `build_report` on solved inputs of
tests/support/solver_inputs.py; layer: tests.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import dataclasses
from datetime import date

from app.domain.optimization import RunResult
from app.optimization.explain import build_report
from app.optimization.formatting import EN_DASH
from app.optimization.model import Constraints, Dependency, Employee, SkillRequirement, Task
from app.optimization.outcome import Conflict, Plan, ScheduledTask, SolveOutcome
from app.optimization.resources import build_resources
from app.optimization.solver import solve
from tests.support.solver_inputs import FAST, MONDAY, SAP, make_input, with_constraints


def test_skill_reassignment_report() -> None:
    """A skill gap moves Build to Lisa (faster at level 5), with KPIs, reasons and rows to match."""
    planning_input = make_input(
        tasks=(
            Task("a", "1.1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.1.2", "Build", 10, owner_id="e1", skills=(SAP,)),
        ),
        constraints=Constraints(budget_total_cents=2_000_000, must_finish_by=date(2026, 10, 30)),
        deliverables={"1.1": "Customer data"},
    )
    report = build_report(planning_input, solve(planning_input, FAST))
    assert report.status == "optimal"
    assert report.solver.startswith("OR-Tools CP-SAT 9.")
    # Lisa (SAP 5, one level above the minimum 4) is 10% faster: 72 h in 9 days (D5).
    assert report.expected_cost_cents == 40 * 10_000 + 72 * 12_000
    assert report.completion_date == date(2026, 10, 22)
    assert report.iis is None
    assert report.error is None
    result = report.result
    assert result is not None
    RunResult.model_validate(result)  # shape matches the API schema

    kpis = {kpi["label"]: kpi for kpi in result["kpis"]}
    assert kpis["Budget"] == {"label": "Budget", "value": "$12,640", "sub": "$7,360 under budget"}
    assert kpis["Completion Date"]["value"] == "Oct 22, 2026"
    assert kpis["Completion Date"]["sub"] == "8 days ahead of target"
    assert kpis["Resource Utilization"]["sub"] == "Average across 2 resources"
    assert kpis["Critical Path"] == {
        "label": "Critical Path",
        "value": "3 weeks",
        "sub": "Design → Build chain",
    }
    assert "Solve" not in kpis

    assert result["reassignments"] == [
        {
            "task": "1.1.2 Build",
            "current": "Mark Chen (100% util)",
            "recommended": "Lisa Park (40h/wk)",
            "reason": "Skill gap",
        }
    ]
    assert [change["delta"] for change in result["scheduleChanges"]] == ["—", "—"]
    assert result["scheduleChanges"][1]["original"] == f"Wk 2{EN_DASH}3"
    assert result["scheduleChanges"][0]["reason"] == "No change needed"
    assert result["scheduleChanges"][1]["reason"] == (
        "Lisa Park (level 5): 9 working days instead of 10"
    )
    assert result["utilization"] == [
        {"name": "Mark Chen", "before": 100, "after": 36},
        {"name": "Lisa Park", "before": 0, "after": 64},
    ]
    assert report.critical_path == [
        {"id": "a", "code": "1.1.1", "name": "Design"},
        {"id": "b", "code": "1.1.2", "name": "Build"},
    ]

    timeline = {row["wbsNodeId"]: row for row in result["schedule"]}
    assert timeline["b"] == {
        "wbsNodeId": "b",
        "code": "1.1.2",
        "name": "Build",
        "deliverableCode": "1.1",
        "deliverableName": "Customer data",
        "start": "2026-10-12",
        "end": "2026-10-22",
        "baselineStart": "2026-10-12",
        "baselineEnd": "2026-10-23",
        "assignee": "Lisa Park",
        "done": False,
    }
    assert result["summary"]["completionDate"] == "2026-10-22"
    # Both work full days, so the busiest day is at 100% (Mark is listed first).
    assert result["summary"]["peakUtilization"] == {"name": "Mark Chen", "percent": 100}
    assert result["summary"]["softViolations"] == []

    rows = {assignment.wbs_node_id: assignment for assignment in report.assignments}
    assert rows["b"].employee_id == "e2"
    assert rows["b"].baseline_employee_id == "e1"
    assert (rows["b"].start_week, rows["b"].end_week) == (1, 2)
    assert rows["b"].hours_per_week == 40.0
    assert [recommendation.type for recommendation in report.recommendations] == [
        "reassignment",
        "schedule_change",
    ]


def test_peak_utilization_is_the_busiest_day_not_an_average() -> None:
    """The peak is the busiest day (100%) though the average is 20%."""
    people = [f"e{number}" for number in range(1, 6)]
    planning_input = make_input(
        tasks=tuple(
            Task(f"t{number}", f"1.1.{number}", f"Step {number}", 1, owner_id=person)
            for number, person in enumerate(people, start=1)
        ),
        employees=tuple(
            Employee(person, f"Chain {number}", hourly_rate_cents=10_000)
            for number, person in enumerate(people, start=1)
        ),
        dependencies=tuple(Dependency(f"t{number}", f"t{number + 1}") for number in range(1, 5)),
    )
    result = build_report(planning_input, solve(planning_input, FAST)).result
    assert result is not None
    assert {row["after"] for row in result["utilization"]} == {20}
    kpis = {kpi["label"]: kpi for kpi in result["kpis"]}
    assert kpis["Resource Utilization"]["value"] == "20%"  # labelled as the average
    assert result["summary"]["peakUtilization"] == {"name": "Chain 1", "percent": 100}


def test_over_budget_and_late_kpis() -> None:
    """A soft over-budget, late plan says by how much in the KPIs and the summary."""
    planning_input = with_constraints(
        make_input(),
        budget_total_cents=100_000,
        budget_mode="soft",
        must_finish_by=date(2026, 10, 9),
        deadline_mode="soft",
    )
    result = build_report(planning_input, solve(planning_input, FAST)).result
    assert result is not None
    kpis = {kpi["label"]: kpi for kpi in result["kpis"]}
    assert kpis["Budget"]["sub"] == "$11,000 over budget"
    assert kpis["Completion Date"]["sub"] == "14 days over target"
    summary = result["summary"]
    assert summary["daysLate"] == 14
    assert summary["deadlineMode"] == "soft"
    assert summary["softViolations"][:2] == [
        "Go-live 14 days after Oct 9, 2026",
        "$11,000 over the budget",
    ]


def test_no_budget_no_deadline_kpis() -> None:
    """Without a budget or deadline the KPIs say none is set."""
    result = build_report(make_input(), solve(make_input(), FAST)).result
    assert result is not None
    kpis = {kpi["label"]: kpi["sub"] for kpi in result["kpis"]}
    assert kpis["Budget"] == "No budget set"
    assert kpis["Completion Date"] == "No deadline set"


def test_resource_conflict_moves_task_and_reassigns() -> None:
    """An over-allocated owner loses a task to an idle person."""
    # Both tasks owned by one full-time person; a second person is idle.
    planning_input = make_input(
        tasks=(
            Task("a", "1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.2", "Backend", 5, owner_id="e1"),
            Task("c", "1.3", "Frontend", 5, owner_id="e1"),
        ),
        employees=(
            Employee("e1", "Mark Chen", hourly_rate_cents=10_000),
            Employee("e2", "Lisa Park", hourly_rate_cents=10_000),
        ),
        dependencies=(Dependency("a", "c"),),
        constraints=Constraints(capacity_mode="hard"),
    )
    report = build_report(planning_input, solve(planning_input, FAST))
    result = report.result
    assert result is not None
    (row,) = result["reassignments"]
    assert row["current"] == "Mark Chen (150% util)"
    assert row["reason"] == "Over-allocation"
    assert all(change["delta"] == "—" for change in result["scheduleChanges"])


def test_dependency_cascade_and_waits_reasons() -> None:
    """Moved tasks say whether they wait for a person or follow a dependency."""
    # One person, hard capacity. Delaying Beta+Gamma by 3 days beats delaying
    # Alpha by 10, so Beta waits for Alpha and Gamma cascades from Beta.
    planning_input = make_input(
        tasks=(
            Task("a", "1.1", "Alpha", 3, owner_id="e1"),
            Task("b", "1.2", "Beta", 5, owner_id="e1"),
            Task("c", "1.3", "Gamma", 5, owner_id="e1"),
        ),
        employees=(Employee("e1", "Solo", hourly_rate_cents=1),),
        dependencies=(Dependency("b", "c"),),
        constraints=Constraints(capacity_mode="hard"),
    )
    result = build_report(planning_input, solve(planning_input, FAST)).result
    assert result is not None
    assert [
        (change["name"], change["delta"], change["reason"]) for change in result["scheduleChanges"]
    ] == [
        ("Alpha", "—", "No change needed"),
        ("Beta", "+3d", "Waits for Solo to finish Alpha"),
        ("Gamma", "+3d", "Dependency cascade from Beta"),
    ]
    assert result["reassignments"] == []


def test_contractor_hire_recommendation() -> None:
    """A skill gap a contractor fills is a hire recommendation with hours and cost."""
    planning_input = make_input(
        tasks=(
            Task("b", "1.2", "SAP Integration", 5, skills=(SkillRequirement("sap", "SAP", 5),)),
        ),
        employees=(Employee("e1", "Mark Chen", hourly_rate_cents=10_000),),
        dependencies=(),
        constraints=Constraints(contractor_rate_cents=15_000),
    )
    report = build_report(planning_input, solve(planning_input, FAST))
    result = report.result
    assert result is not None
    assert result["reassignments"][0]["current"] == "Unassigned"
    assert result["reassignments"][0]["recommended"] == "Contractor: SAP specialist (40h/wk)"
    assert result["reassignments"][0]["reason"] == "Skill gap"
    utilization = {row["name"]: row for row in result["utilization"]}
    assert utilization["Contractor: SAP specialist"]["before"] == 0
    hire = [
        recommendation for recommendation in report.recommendations if recommendation.type == "hire"
    ]
    assert hire[0].after_value == "Contractor: SAP specialist"
    assert hire[0].reason == "Skill gap: about 40 hours, est. $6,000"
    row = report.assignments[0]
    assert row.employee_id is None
    assert row.contractor_label == "Contractor: SAP specialist"


def test_infeasible_report_has_iis_and_no_result() -> None:
    """An infeasible run reports its conflicts and no plan."""
    planning_input = with_constraints(make_input(), budget_total_cents=1)
    report = build_report(planning_input, solve(planning_input, FAST))
    assert report.status == "infeasible"
    assert report.result is None
    assert report.iis is not None
    assert report.iis[0]["name"] == "Budget cap: $0"
    assert report.assignments == []


def _outcome(planning_input: object, **fields: object) -> SolveOutcome:
    """Return a timed-out solve outcome for the input, changed by `fields`."""
    from app.optimization.baseline import baseline_plan
    from app.optimization.model import PlanningInput

    assert isinstance(planning_input, PlanningInput)
    base: dict[str, object] = {
        "status": "timeout",
        "solver": "OR-Tools CP-SAT x",
        "solver_version": "x",
        "wall_seconds": 20.0,
        "resources": build_resources(planning_input),
        "baseline": baseline_plan(planning_input),
    }
    base.update(fields)
    return SolveOutcome(**base)  # type: ignore[arg-type]


def test_timeout_and_time_limited_feasible() -> None:
    """A timeout reports its message; a time-limited plan reports its gap to optimal."""
    planning_input = make_input()
    report = build_report(
        planning_input, _outcome(planning_input, message="No feasible plan was found.")
    )
    assert (report.status, report.result, report.iis) == ("timeout", None, None)
    assert report.error == "No feasible plan was found."
    assert report.runtime_ms == 20_000

    plan = Plan({"a": ScheduledTask("a", 0, 5, "e1"), "b": ScheduledTask("b", 5, 15, "e1")})
    feasible = _outcome(
        planning_input,
        status="feasible",
        plan=plan,
        objective=110.0,
        best_bound=100.0,
        hit_time_limit=True,
    )
    report = build_report(planning_input, feasible)
    assert report.mip_gap is not None
    assert abs(report.mip_gap - 1 / 11) < 1e-9
    assert report.result is not None
    solve_kpi = report.result["kpis"][-1]
    assert solve_kpi["label"] == "Solve"
    assert solve_kpi["sub"] == "Best plan found, within 9.1% of optimal"


def test_conflict_dataclass_is_plain() -> None:
    """Conflicts serialize as plain name and explanation."""
    assert dataclasses.asdict(Conflict("a", "b")) == {"name": "a", "explanation": "b"}
    assert MONDAY.weekday() == 0
