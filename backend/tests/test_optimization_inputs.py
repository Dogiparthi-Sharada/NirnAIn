"""The optimizer's confirmation sheet: facts, rows, sources and blockers."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from app.optimization.input_sheet import blocking_detail, build_sheet
from app.optimization.inputs import (
    InputFacts,
    MilestoneFact,
    SkillFact,
    facts_from_input,
    inputs_hash,
    quote_states_amount,
)
from app.optimization.model import Allocation, Employee, Task
from tests.support.solver_inputs import SAP, make_input, with_constraints


def _ready_facts(**changes: Any) -> InputFacts:
    facts = InputFacts(
        work_packages=15,
        effort_days=225,
        confirmed_ai_estimates=12,
        dependency_types={"FS": 4, "SS": 1},
        budget_cents=37_500_000,
        budget_quote="Budget approved at $375K",
        must_finish_by=date(2025, 9, 30),
        deadline_answer="unknown",
        specialist_answer="yes",
        team=["Mark Chen", "Lisa Park"],
        team_from_project=True,
        skills=[SkillFact("SAP Integration", True, False), SkillFact("Python", False, True)],
        linked_skills=frozenset({"sap integration"}),
        milestones=[
            MilestoneFact("Phase 1", date(2025, 4, 15), "soft"),
            MilestoneFact("Phase 2", date(2025, 7, 31), "soft"),
        ],
    )
    return dataclasses.replace(facts, **changes)


def test_ready_sheet_rows_and_sources() -> None:
    sheet = build_sheet(_ready_facts())
    assert sheet.ready
    assert (sheet.blocking, sheet.missing) == ([], [])
    rows = {row.key: (row.value, row.source, row.from_) for row in sheet.rows}
    assert list(rows) == [
        "workPackages",
        "dependencies",
        "budget",
        "deadline",
        "specialists",
        "team",
        "employeeCost",
        "skills",
        "contingency",
        "workingDays",
        "milestones",
    ]
    assert rows["workPackages"] == (
        "15 work packages · 45 weeks of effort",
        "confirmed",
        "Step 4",
    )
    assert rows["dependencies"][0] == "5 (4 finish-to-start, 1 start-to-start)"
    assert rows["budget"] == (
        "$375,000 · must hold",
        "stated",
        "\u201cBudget approved at $375K\u201d",
    )
    assert rows["deadline"] == (
        "Sep 30, 2025 · hard (you said Don\u2019t know)",
        "assumed",
        "Question on step 5",
    )
    assert rows["specialists"][:2] == ("Allowed · $175/h (estimated)", "confirmed")
    assert rows["team"] == ("Mark Chen, Lisa Park · capacity can flex", "confirmed", "Step 5")
    assert rows["employeeCost"] == ("No employees on the team", "default", "Company record")
    assert rows["skills"][0] == "1 · nobody has SAP Integration yet · must hold"
    assert rows["contingency"][:2] == ("0%", "default")
    assert rows["milestones"] == ("Apr 15 and Jul 31 · soft", "yours", "Step 5")


def test_a_required_skill_no_package_uses_is_a_warning_not_a_blocker() -> None:
    inp = make_input(tasks=(Task("a", "1.1.1", "SAP setup", 5, skills=(SAP,)),))
    okta = SkillFact("Okta SSO", True, False)
    facts = facts_from_input(
        inp, skills=[SkillFact("sap", True, True), okta, SkillFact("Figma", False, False)]
    )
    assert facts.unlinked_skills == ["Okta SSO"]  # SAP is used; Figma is optional
    sheet = build_sheet(dataclasses.replace(_ready_facts(), **_skills_of(facts)))
    assert sheet.ready
    rows = {row.key: row for row in sheet.rows}
    warning = rows["skillUnlinked:Okta SSO"]
    assert (warning.label, warning.value, warning.source, warning.from_) == (
        "Okta SSO",
        "Okta SSO is required but no work package uses it. Tag the packages on step 4.",
        "needs_you",
        "Step 4",
    )
    assert list(rows).index("skillUnlinked:Okta SSO") == list(rows).index("skills") + 1


def _skills_of(facts: InputFacts) -> dict[str, Any]:
    return {"skills": facts.skills, "linked_skills": facts.linked_skills}


def test_unconfirmed_estimates_block_the_run() -> None:
    sheet = build_sheet(_ready_facts(unconfirmed_estimates=3))
    assert not sheet.ready
    assert sheet.missing == ["estimates"]
    assert sheet.blocking == ["Confirm 3 AI estimates on step 4"]
    row = sheet.rows[0]
    assert row.value == "15 work packages · 3 AI estimates not confirmed"
    assert row.source == "needs_you"


def test_unanswered_questions_and_empty_team_block() -> None:
    sheet = build_sheet(
        _ready_facts(deadline_answer=None, specialist_answer=None, team=[], work_packages=0)
    )
    assert sheet.missing == ["workPackages", "deadline", "specialist", "team"]
    assert blocking_detail(sheet.blocking) == (
        "Add work packages with durations on step 4, answer the deadline question on step 5, "
        "answer the specialist question on step 5 and add your team on step 5 "
        "before running the optimizer."
    )
    # Without a skill gap the specialist question doesn't need an answer.
    no_gap = _ready_facts(specialist_answer=None, skills=[SkillFact("Python", True, True)])
    assert build_sheet(no_gap).ready


def test_facts_from_input_and_org_fallback_team() -> None:
    inp = make_input(
        tasks=(
            Task("a", "1.1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.1.2", "SAP setup", 10, skills=(SAP,)),
        ),
        employees=make_input().employees[:1],
    )
    inp = with_constraints(inp, budget_total_cents=2_000_000, working_days_per_week=6)
    facts = facts_from_input(inp, deadline_answer="hard", contingency_pct=10.0)
    assert (facts.work_packages, facts.effort_days) == (2, 15)
    assert facts.dependency_types == {"FS": 1}
    assert facts.task_skill_gaps == ["SAP"]
    assert facts.team == ["Mark Chen"]
    rows = {row.key: row for row in build_sheet(facts).rows}
    assert rows["team"].source == "default"
    assert rows["team"].value.startswith("No project team yet, so everyone active")
    assert (rows["budget"].value, rows["budget"].from_) == ("$20,000 · must hold", "Step 1")
    assert (rows["contingency"].label, rows["contingency"].value) == (
        "Reserve",
        "10% = $2,000 held back",
    )
    assert rows["contingency"].source == "yours"
    assert (rows["workingDays"].value, rows["workingDays"].source) == ("6 per week", "yours")
    assert rows["specialists"].source == "needs_you"


def test_employees_without_a_company_rate_are_shown_at_the_standard_rate() -> None:
    team = ["Priya Raman", "Leo Martins", "Ana Souza", "Dana Lee"]
    staff = team[:3]
    facts = _ready_facts(team=team, employees=staff, standard_rate_employees=staff)
    rows = {row.key: row for row in build_sheet(facts).rows}
    assert (rows["employeeCost"].label, rows["employeeCost"].value) == (
        "Employee cost",
        "3 employees at the standard $85/h (no company rate on record)",
    )
    assert (rows["employeeCost"].source, rows["employeeCost"].from_) == (
        "default",
        "Company record",
    )
    some = _ready_facts(team=team, employees=staff, standard_rate_employees=["Ana Souza"])
    row = next(row for row in build_sheet(some).rows if row.key == "employeeCost")
    assert (
        row.value
        == "1 employee at the standard $85/h (no company rate on record) · 2 at their company rate"
    )
    rated = _ready_facts(team=team, employees=staff)
    row = next(row for row in build_sheet(rated).rows if row.key == "employeeCost")
    assert (row.value, row.source) == ("3 employees at their company rate", "confirmed")

    inp = make_input(
        employees=(
            Employee("e1", "Mark Chen", hourly_rate_cents=10_000),
            Employee("e2", "Ana Souza"),
            Employee("e3", "Dana Lee", hourly_rate_cents=15_000, is_contractor=True),
        )
    )
    facts = facts_from_input(inp)
    assert (facts.employees, facts.standard_rate_employees) == (
        ["Mark Chen", "Ana Souza"],
        ["Ana Souza"],
    )


def test_quote_must_name_the_amount() -> None:
    assert quote_states_amount("Budget approved at $375K", 37_500_000)
    assert quote_states_amount("a budget of $375,000 total", 37_500_000)
    assert quote_states_amount("up to $1.2 million", 120_000_000)
    assert not quote_states_amount("Budget approved at $400K", 37_500_000)
    assert not quote_states_amount("the budget is tight", 37_500_000)


def test_inputs_hash_ignores_other_projects_bookings() -> None:
    inp = make_input()
    booked = dataclasses.replace(inp, allocations=(Allocation("e1", date(2026, 10, 12), 8),))
    assert inputs_hash(inp) == inputs_hash(booked)
    assert inputs_hash(inp) != inputs_hash(with_constraints(inp, budget_total_cents=1))
    assert inputs_hash(inp) != inputs_hash(with_constraints(inp, contingency_pct=10))
    first, second = inp.employees
    repriced = dataclasses.replace(
        inp, employees=(dataclasses.replace(first, hourly_rate_cents=None), second)
    )
    assert inputs_hash(inp) != inputs_hash(repriced)  # editing a rate invalidates the sheet
    assert len(inputs_hash(inp)) == 64


def test_applying_a_plan_does_not_change_the_inputs_hash() -> None:
    """Apply writes the plan's assignees as owners and done work's dates; the plan stays current."""
    inp = make_input()
    first_task, *other_tasks = inp.tasks
    after_apply = dataclasses.replace(
        inp,
        tasks=(
            dataclasses.replace(
                first_task,
                owner_id=inp.employees[-1].id,
                applied_start=date(2026, 10, 12),
                applied_end=date(2026, 10, 16),
            ),
            *other_tasks,
        ),
    )
    assert inputs_hash(inp) == inputs_hash(after_apply)
    longer = dataclasses.replace(
        inp,
        tasks=(
            dataclasses.replace(first_task, duration_days=first_task.duration_days + 5),
            *other_tasks,
        ),
    )
    assert inputs_hash(inp) != inputs_hash(longer)  # a real input edit still invalidates the plan
