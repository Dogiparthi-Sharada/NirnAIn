"""Tests that skill level changes how long a task takes (round-2 decision D5).

Purpose: Each level above a package's minimum is 10% faster (at most 30%); each level below, which
only "Can flex" skills allow, is 25% slower. The pace scales the hours, so it moves the duration and
the cost together, combines with the part-time stretch and is named in the reason.
How it fits: Pure tests of app/optimization/load.py (`pace_percent`, `pace_level`), the solver and
the report; layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import pytest

from app.optimization.explain import build_report
from app.optimization.model import Constraints, Employee, SkillRequirement, Task
from app.optimization.outcome import Resource
from app.optimization.solver import solve
from app.optimization.speed import pace_level, pace_percent
from tests.support.solver_inputs import FAST, make_input

ETL = SkillRequirement("etl", "ETL", 2)
DESIGN = SkillRequirement("design", "Data modelling", 3)
BASIC = SkillRequirement("etl", "ETL", 1)


def _person(skills: dict[str, int], kind: str = "employee") -> Resource:
    """Return Priya Raman as a resource with the given skill levels."""
    return Resource("p", "Priya Raman", kind, 40, 10_000, skills)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("skills", "pace", "level"),
    [
        ({"etl": 2, "design": 3}, 100, None),  # at the minimum: the WBS pace
        ({"etl": 5, "design": 4}, 90, 4),  # the weakest skill sets the pace
        ({"etl": 5, "design": 5}, 80, 5),
        ({"etl": 1, "design": 2}, 150, 1),  # 2 levels below: 2 x 25% slower
    ],
)
def test_pace_follows_the_level_against_the_minimum(
    skills: dict[str, int], pace: int, level: int | None
) -> None:
    """The weakest skill against its minimum sets the pace; contractors keep the WBS pace."""
    task = Task("t", "1.1.1", "Pipeline", 10, skills=(ETL, DESIGN))
    assert pace_percent(_person(skills), task) == pace
    assert pace_level(_person(skills), task) == level
    # At most 30% faster, however far above the minimum.
    assert pace_percent(_person({"etl": 5}), Task("v", "1.1.3", "Load", 5, skills=(BASIC,))) == 70
    # Contractor profiles and work without skills stay at the WBS pace.
    assert pace_percent(_person(skills, kind="contractor"), task) == 100
    assert pace_percent(_person(skills), Task("u", "1.1.2", "Docs", 5)) == 100


def _one_task(level: int, hours: float = 40, skill_mode: str = "hard") -> tuple[int, int, str]:
    """Priya alone on a 10-day ETL package (minimum level 2): (days, cost, reason)."""
    planning_input = make_input(
        tasks=(Task("a", "1.1.1", "Pipeline", 10, owner_id="p", skills=(ETL,)),),
        employees=(
            Employee("p", "Priya Raman", hours, hourly_rate_cents=10_000, skills={"etl": level}),
        ),
        dependencies=(),
        constraints=Constraints(skill_mode=skill_mode),  # type: ignore[arg-type]
    )
    outcome = solve(planning_input, FAST)
    assert outcome.plan is not None
    report = build_report(planning_input, outcome)
    assert report.result is not None
    assert report.expected_cost_cents is not None
    (change,) = report.result["scheduleChanges"]
    return outcome.plan.makespan, report.expected_cost_cents, change["reason"]


def test_a_skilled_person_is_faster_and_cheaper() -> None:
    """Level 5 on a level-2 package is 30% faster and cheaper, and says so."""
    # Level 5 on a level-2 package: 30% faster, 56 h instead of 80.
    assert _one_task(5) == (7, 56 * 10_000, "Priya Raman (level 5): 7 working days instead of 10")
    assert _one_task(2) == (10, 80 * 10_000, "No change needed")


def test_skill_pace_combines_with_the_part_time_stretch() -> None:
    """The skill pace combines with the part-time stretch."""
    # 56 h at 3.2 h a day (16 h/week): 18 working days instead of 25 at level 2.
    assert _one_task(5, hours=16) == (
        18,
        56 * 10_000,
        "Priya Raman (level 5) at 16h/week: 18 working days instead of 10",
    )
    assert _one_task(2, hours=16)[:2] == (25, 80 * 10_000)


def test_below_the_minimum_is_slower_when_skills_can_flex() -> None:
    """One level below the minimum is 25% slower when skills can flex."""
    # One level below: 25% slower, 100 h over 13 days (12.5 rounded up).
    days, cost, reason = _one_task(1, skill_mode="soft")
    assert (days, cost) == (13, 100 * 10_000)
    assert reason == "Priya Raman (level 1): 13 working days instead of 10"
