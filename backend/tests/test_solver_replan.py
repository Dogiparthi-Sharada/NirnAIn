"""Re-plans start from today and leave finished work where it happened (round-2 D4).

QA: marking a work package Done on step 7 and re-planning failed with 422,
and re-plans could schedule unfinished work in the past. Here `today` is
injected through `PlanningInput.today` (the repository sets it from the
request date), and `durationDays: 0` marks a work package done.
"""

from __future__ import annotations

import dataclasses
from datetime import date

from app.optimization.explain import build_report
from app.optimization.inputs import inputs_hash
from app.optimization.model import PlanningInput
from app.optimization.overrides import apply_overrides, with_plan_in_force
from app.optimization.solver import solve
from tests.support.solver_inputs import FAST, MONDAY, make_input

TODAY = date(2026, 10, 21)  # a Wednesday: working day 12 of a project started Mon Oct 5


def _replan(**overrides: object) -> PlanningInput:
    """Design (5 d) then Build (10 d), both Mark's; the plan in force had them back to back."""
    applied = with_plan_in_force(
        dataclasses.replace(make_input(), today=TODAY),
        {"a": (MONDAY, date(2026, 10, 9)), "b": (date(2026, 10, 12), date(2026, 10, 23))},
        None,
    )
    return apply_overrides(applied, overrides)


def test_unfinished_work_never_starts_before_today() -> None:
    outcome = solve(_replan(), FAST)
    assert outcome.plan is not None
    design, build = outcome.plan.tasks["a"], outcome.plan.tasks["b"]
    assert (design.start, build.start) == (12, 17)  # Oct 21 and Oct 28, not Oct 5
    assert outcome.baseline.tasks["a"].start == 12  # the "before" plan starts today too
    report = build_report(_replan(), outcome)
    assert report.result is not None
    assert report.result["schedule"][0]["start"] == "2026-10-21"


def test_done_work_stays_put_and_partly_done_work_resumes_today() -> None:
    # Design is done; Build is half done: 5 working days left.
    inp = _replan(durationDays={"a": 0, "b": 5})
    outcome = solve(inp, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    design, build = outcome.plan.tasks["a"], outcome.plan.tasks["b"]
    assert (design.start, design.end, design.resource_id) == (0, 5, "e1")  # as applied
    # Its link to Design is already met, so Build resumes today, for its remaining 5 days.
    assert (build.start, build.end) == (12, 17)
    report = build_report(inp, outcome)
    assert report.result is not None
    timeline = {row["wbsNodeId"]: row for row in report.result["schedule"]}
    assert (timeline["a"]["start"], timeline["a"]["end"], timeline["a"]["done"]) == (
        "2026-10-05",
        "2026-10-09",
        True,
    )
    assert timeline["b"]["done"] is False
    changes = {row["wbs"]: row for row in report.result["scheduleChanges"]}
    assert (changes["1.1.1"]["reason"], changes["1.1.1"]["delta"]) == ("Done", "—")
    # Only the 40 hours left on Build cost anything.
    assert report.expected_cost_cents == 40 * 10_000
    assert report.completion_date == date(2026, 10, 27)


def test_done_without_a_plan_in_force_is_marked_where_planning_starts() -> None:
    inp = apply_overrides(
        dataclasses.replace(make_input(), today=TODAY), {"durationDays": {"a": 0}}
    )
    outcome = solve(inp, FAST)
    assert outcome.plan is not None
    assert (outcome.plan.tasks["a"].start, outcome.plan.tasks["a"].end) == (12, 12)
    assert outcome.plan.tasks["b"].start == 12


def test_today_is_frozen_with_the_run_but_not_part_of_the_inputs_hash() -> None:
    inp = _replan(durationDays={"a": 0})
    snapshot = inp.to_snapshot()
    assert (snapshot["today"], snapshot["tasks"][0]["done"]) == ("2026-10-21", True)
    assert snapshot["tasks"][0]["applied_end"] == "2026-10-09"
    assert PlanningInput.from_snapshot(snapshot) == inp
    tomorrow = dataclasses.replace(make_input(), today=date(2026, 10, 22))
    assert inputs_hash(dataclasses.replace(make_input(), today=TODAY)) == inputs_hash(tomorrow)


def test_all_work_done_still_gives_a_plan() -> None:
    inp = _replan(durationDays={"a": 0, "b": 0})
    outcome = solve(inp, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    assert outcome.plan.makespan == 15
    assert build_report(inp, outcome).completion_date == date(2026, 10, 23)
