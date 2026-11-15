"""Every blocker of an infeasible run, and the combined way out (round 3, R6).

The pack's M03 meeting: the board makes April 30 a hard go-live, James's
SOC2 Controls qualification is withdrawn, nobody on the team has SAP
Integration and outside specialists are prohibited. Each of these blocks
the plan on its own, so one run must name all three, and since no single
option fixes them all, offer to change them together.
"""

from __future__ import annotations

from datetime import date

from app.optimization.blockers import MAX_BLOCKERS
from app.optimization.model import (
    Constraints,
    Dependency,
    Employee,
    PlanningInput,
    SkillRequirement,
    Task,
)
from app.optimization.remedies import apply_remedy, price_remedies
from app.optimization.solver import SolveOptions, solve
from tests.support.solver_inputs import make_input

QUICK = SolveOptions(time_limit_s=10, workers=1)


def _skill(name: str) -> SkillRequirement:
    return SkillRequirement(name.lower().replace(" ", "-"), name, 3)


def _package(task_id: str, code: str, name: str, hours: int, skill: str, owner: str | None) -> Task:
    return Task(
        task_id, code, name, hours // 8, effort_hours=hours, owner_id=owner, skills=(_skill(skill),)
    )


def _person(person_id: str, name: str, hours: float, rate: int, skills: list[str]) -> Employee:
    return Employee(
        person_id, name, hours, rate, skills={_skill(skill).skill_id: 3 for skill in skills}
    )


def m03_input() -> PlanningInput:
    """The M03 project's open work on Feb 1, 2027, after Priya left the team."""
    tasks = (
        _package("W02", "1.1.2", "SAP connector", 128, "SAP Integration", None),
        _package("W03", "1.1.3", "Internal database ingestion", 216, "Spark", "mark"),
        _package("W05", "1.2.2", "ETL pipeline", 160, "Spark", "mark"),
        _package("W06", "1.3.1", "BI tool setup", 32, "Power BI", "tom"),
        _package("W07", "1.3.2", "Standard reports", 128, "Power BI", "tom"),
        _package("W10", "1.4.2", "Audit logging", 64, "Audit Logging", "james"),
        _package("W11", "1.4.3", "Control evidence", 80, "SOC2 Controls", "james"),
        _package("W13", "1.5.1", "UAT", 120, "QA Testing", "sara"),
        _package("W14", "1.5.2", "User training", 60, "Training", "tom"),
        _package("W15", "1.5.3", "Production rollout", 40, "DevOps", "lisa"),
    )
    links = (
        ("W02", "W05"),
        ("W03", "W05"),
        ("W05", "W06"),
        ("W06", "W07"),
        ("W10", "W11"),
        ("W05", "W11"),
        ("W07", "W13"),
        ("W11", "W13"),
        ("W13", "W14"),
        ("W14", "W15"),
    )
    team = (
        _person("sara", "Sara Kim", 40, 15_000, ["Data Modeling", "Spark", "QA Testing"]),
        _person("mark", "Mark Chen", 24, 10_000, ["Salesforce Integration", "Spark"]),
        _person("tom", "Tom Rivera", 32, 9_500, ["Power BI", "Training", "QA Testing"]),
        _person("lisa", "Lisa Park", 32, 11_000, ["DevOps", "Disaster Recovery"]),
        _person("james", "James Wu", 16, 13_000, ["RBAC", "Audit Logging"]),  # SOC2 withdrawn
    )
    return PlanningInput(
        project_id="m03",
        project_name="Enterprise Data Platform QA",
        start_date=date(2027, 2, 1),
        tasks=tasks,
        employees=team,
        dependencies=tuple(Dependency(before, after) for before, after in links),
        constraints=Constraints(
            budget_total_cents=20_000_000,
            contingency_pct=10,
            must_finish_by=date(2027, 4, 30),
            deadline_mode="hard",
            capacity_mode="hard",
            skill_mode="hard",
            allow_contractors=False,
        ),
        today=date(2027, 2, 1),
    )


def test_one_run_names_the_deadline_and_both_missing_skills() -> None:
    outcome = solve(m03_input(), QUICK)

    assert outcome.status == "infeasible"
    named = {conflict.name: conflict.explanation for conflict in outcome.conflicts}
    assert list(named)[:3] == [
        "Deadline: must finish by Apr 30, 2027",
        "Skill: 1.1.2 SAP connector requires SAP Integration ≥ 3",
        "Skill: 1.4.3 Control evidence requires SOC2 Controls ≥ 3",
    ]
    assert named["Skill: 1.4.3 Control evidence requires SOC2 Controls ≥ 3"] == (
        "No one on the team has SOC2 Controls at level 3 or higher, and contractors are turned off."
    )
    assert named["Deadline: must finish by Apr 30, 2027"].startswith(
        "The dependency chain alone runs until"
    )
    # The rest are the dependencies that make the deadline impossible.
    assert all(name.startswith("Dependency: ") for name in list(named)[3:])


def test_blockers_are_named_up_to_the_limit() -> None:
    gaps = [f"Skill {number}" for number in range(MAX_BLOCKERS + 1)]
    inp = make_input(
        tasks=tuple(
            _package(f"t{number}", f"1.{number}", f"Package {number}", 8, gap, None)
            for number, gap in enumerate(gaps)
        ),
        dependencies=(),
        constraints=Constraints(allow_contractors=False),
    )
    conflicts = solve(inp, QUICK).conflicts
    assert len(conflicts) == MAX_BLOCKERS
    assert all(conflict.name.startswith("Skill: ") for conflict in conflicts)


def test_no_single_option_works_so_they_are_combined() -> None:
    inp = m03_input()

    (remedy,) = price_remedies(inp, QUICK)

    assert (remedy.id, remedy.title) == ("combined", "Change these together")
    assert remedy.completion_date is not None
    assert remedy.completion_date > date(2027, 4, 30)
    assert remedy.detail.startswith("No single change is enough; together these get a plan")
    deadline, specialist = remedy.changes
    assert deadline.kind == "deadline"
    assert deadline.label.startswith("Move go-live from Apr 30, 2027 to ")
    assert specialist.kind == "specialist"
    assert specialist.label == (
        "Bring in a contractor: 208 hours of the work at $175/h (estimate), covering "
        "SAP Integration and SOC2 Controls, which nobody on the team has"
    )
    assert remedy.to_json()["changes"][0] == {"kind": "deadline", "label": deadline.label}

    # "Use this option" re-runs with all of those changes and gets that plan.
    rerun = solve(apply_remedy(inp, "combined"), QUICK)
    assert rerun.plan is not None
    assert rerun.status == "optimal"
