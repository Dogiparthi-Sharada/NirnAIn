"""Tests of the remedies for infeasible plans and of per-run overrides.

Purpose: When no plan meets the limits, each remedy (a contractor, a later go-live, slipping
milestones, overtime, more budget) must be solved and priced, and each one not offered must say
why; overrides change only one run.
How it fits: Pure tests of app/optimization/remedies.py and overrides.py on tiny hand-built inputs;
layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date

from app.domain.optimization import RunOverrides
from app.optimization.model import Constraints, Employee, Milestone, PlanningInput, Task
from app.optimization.outcome import Conflict, Plan, SolveOutcome
from app.optimization.overrides import apply_overrides, with_baseline
from app.optimization.remedies import apply_remedy, price_remedies, variants
from app.optimization.remedy_options import NO_PLAN, OUT_OF_TIME, no_plan_reason, skipped_reasons
from app.optimization.solver import SolveOptions, solve
from tests.support.solver_inputs import SAP, make_input, with_constraints

TINY = SolveOptions(time_limit_s=2, workers=1)


def _sap_gap_input() -> PlanningInput:
    """Mark Chen alone; the SAP work package needs a skill nobody has; no specialists."""
    return make_input(
        tasks=(
            Task("a", "1.1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.1.2", "SAP setup", 10, skills=(SAP,)),
        ),
        employees=(Employee("e1", "Mark Chen", hourly_rate_cents=10_000),),
        constraints=Constraints(
            allow_contractors=False,
            must_finish_by=date(2026, 10, 23),
            skill_mode="hard",
        ),
    )


def test_missing_skill_offers_a_priced_contractor() -> None:
    """A missing skill offers a contractor, priced for the whole relaxed plan."""
    planning_input = _sap_gap_input()
    assert solve(planning_input, TINY).status == "infeasible"

    remedies = price_remedies(planning_input, TINY).offered

    # Moving the deadline alone can't fix a missing skill, so it is not offered.
    assert [remedy.id for remedy in remedies] == ["specialist"]
    contractor = remedies[0]
    # 80 h of SAP work at the $175/h estimate; 40 h of design at $100/h. An
    # infeasible run has no plan to compare with: the whole relaxed plan's cost.
    assert contractor.title == "Bring in a contractor"
    assert contractor.cost_cents == 80 * 17_500 + 40 * 10_000
    assert contractor.completion_date == date(2026, 10, 23)
    assert contractor.detail == (
        "A contractor takes 80 hours of the work at $175/h (estimate), covering SAP, "
        "which nobody on the team has · go-live Oct 23, 2026 (on the Oct 23 target)"
    )
    assert contractor.to_json()["completionDate"] == "2026-10-23"


def test_tight_hard_deadline_offers_moving_go_live() -> None:
    """A tight hard deadline offers moving go-live by the days needed."""
    planning_input = with_constraints(
        make_input(), must_finish_by=date(2026, 10, 16), deadline_mode="hard"
    )
    assert solve(planning_input, TINY).status == "infeasible"

    (remedy,) = price_remedies(planning_input, TINY).offered

    assert remedy.id == "move_deadline"
    assert remedy.title == "Move go-live to Oct 23 (+7 days)"
    assert remedy.completion_date == date(2026, 10, 23)
    assert remedy.detail == "Go-live Oct 23, 2026 · no contractor · about $12,000"


def test_hard_milestone_is_relaxed_with_the_deadline() -> None:
    """A hard milestone is relaxed together with the deadline."""
    planning_input = make_input(
        milestones=(Milestone("m", "Design done", date(2026, 10, 6), "hard", ("a",)),),
    )
    assert [remedy_id for remedy_id, _ in variants(planning_input, solved=False)] == [
        "specialist",
        "move_deadline",
    ]
    (remedy,) = price_remedies(planning_input, TINY).offered
    assert remedy.title == "Let the phase milestones slip"
    assert remedy.detail.endswith("phase milestones may slip (soft)")


def test_a_budget_no_single_option_fixes_is_offered_combined() -> None:
    """A budget no single remedy fixes is offered as a combined change."""
    planning_input = with_constraints(make_input(), budget_total_cents=1, budget_mode="hard")
    # A contractor is always tried, but costs more: still over the cap.
    assert [remedy_id for remedy_id, _ in variants(planning_input, solved=False)] == ["specialist"]
    (remedy,) = price_remedies(planning_input, TINY).offered
    assert remedy.id == "combined"
    assert [(change.kind, change.label) for change in remedy.changes] == [
        ("budget", "Spend $12,000 more: this plan costs $12,000, the budget is $0"),
    ]
    assert remedy.cost_cents == 120 * 10_000


def test_options_tried_but_not_offered_name_the_blockers_they_leave() -> None:
    """The PO's case: the chain runs past the hard deadline and the cheapest staffing is over the
    hard budget. Only "Change these together" gets a plan; the others say which of the run's own
    blockers they leave in place (no extra solve), or why they weren't tried."""
    planning_input = with_constraints(
        make_input(), must_finish_by=date(2026, 10, 16), budget_total_cents=500_000
    )
    base = solve(planning_input, TINY)
    assert base.status == "infeasible"

    priced = price_remedies(planning_input, TINY, conflicts=base.conflicts)

    assert [remedy.id for remedy in priced.offered] == ["combined"]
    assert [entry.to_json() for entry in priced.tried] == [
        {
            "id": "specialist",
            "title": "Bring in a contractor",
            "reason": "Not enough on its own. The dependency chain alone runs until "
            "Oct 23, 2026, and the cheapest staffing costs $12,000, over the $5,000 budget.",
        },
        {
            "id": "overtime",
            "title": "Overtime up to 25% over people's hours",
            "reason": "Not needed: overtime is already allowed under Can flex.",
        },
        {
            "id": "move_deadline",
            "title": "Move go-live",
            "reason": "Not enough on its own. "
            "The cheapest staffing costs $12,000, over the $5,000 budget.",
        },
    ]
    # Without the run's conflicts (an older caller) the reason stays general.
    (first, *_) = price_remedies(planning_input, TINY).tried
    assert first.reason == NO_PLAN


def test_tried_reasons_cap_the_blockers_and_say_when_time_ran_out() -> None:
    """At most three blockers are named, then a count; a variant out of time says so."""
    planning_input = make_input()
    blockers = [
        Conflict(f"Skill {number}", "x", "skill", f"nobody has skill {number}")
        for number in range(5)
    ]
    outcome = SolveOutcome("infeasible", "x", "x", 0.0, (), Plan({}))
    reason = no_plan_reason("move_deadline", outcome, blockers).reason
    assert reason == (
        "Not enough on its own. Nobody has skill 0, nobody has skill 1, nobody has skill 2, "
        "and 2 other limits also stand."
    )
    late = SolveOutcome("timeout", "x", "x", 0.0, (), Plan({}))
    assert no_plan_reason("specialist", late, blockers).reason == OUT_OF_TIME
    assert skipped_reasons(planning_input, ["specialist", "overtime"])[0].reason == (
        "Not needed: there is no go-live date or phase milestone that must hold."
    )


def test_run_with_remedy_applies_it() -> None:
    """Each remedy relaxes the input it names; no remedy keeps the input."""
    planning_input = _sap_gap_input()
    relaxed = apply_remedy(planning_input, "specialist")
    assert relaxed.constraints.allow_contractors is True
    assert relaxed.constraints.contractor_rate_cents == 17_500
    assert solve(relaxed, TINY).status in ("optimal", "feasible")
    overtime = apply_remedy(with_constraints(planning_input, capacity_mode="hard"), "overtime")
    assert overtime.constraints.capacity_mode == "soft"
    assert overtime.constraints.target_penalty_per_day > 0  # it tries to keep the date

    with_milestone = make_input(milestones=(Milestone("m", "M", date(2026, 10, 6)),))
    moved = apply_remedy(with_milestone, "move_deadline")
    assert moved.constraints.deadline_mode == "soft"
    assert [milestone.mode for milestone in moved.milestones] == ["soft"]
    assert apply_remedy(planning_input, None) is planning_input


# --------------------------------------------------------------------------- overrides
def test_overrides_change_only_this_run() -> None:
    """Overrides change budget, dates, durations (effort scales) and hours of this run only."""
    planning_input = make_input(
        tasks=(
            Task("a", "1.1.1", "Design", 5, effort_hours=20, owner_id="e1"),
            Task("b", "1.1.2", "Build", 10, owner_id="e1"),
        )
    )
    changed = apply_overrides(
        planning_input,
        {
            "budgetTotalCents": 5_000_000,
            "mustFinishBy": "2026-12-18",
            "durationDays": {"a": 10, "b": 0, "unknown": 3},
            "hoursPerWeek": {"e2": 16, "nobody": 10},
        },
    )
    assert changed.constraints.budget_total_cents == 5_000_000
    assert changed.constraints.must_finish_by == date(2026, 12, 18)
    design, build = changed.tasks
    assert (design.duration_days, design.effort_hours) == (10, 40.0)  # effort scales
    assert (build.duration_days, build.done, build.hours) == (10, True, 0.0)  # 0 left = done
    assert [employee.weekly_capacity_hours for employee in changed.employees] == [40.0, 16.0]
    assert planning_input.constraints.budget_total_cents is None  # the original is untouched
    assert apply_overrides(planning_input, None) is planning_input
    assert apply_overrides(planning_input, {}) is planning_input


def test_overrides_set_the_deadline_and_budget_modes() -> None:
    """Overrides may set the deadline and budget modes; otherwise they are kept."""
    planning_input = with_constraints(make_input(), deadline_mode="soft", budget_mode="hard")
    changed = apply_overrides(planning_input, {"deadlineMode": "hard", "budgetMode": "soft"})
    assert (changed.constraints.deadline_mode, changed.constraints.budget_mode) == (
        "hard",
        "soft",
    )
    kept = apply_overrides(planning_input, {"mustFinishBy": "2026-12-18"})
    assert (kept.constraints.deadline_mode, kept.constraints.budget_mode) == ("soft", "hard")
    body = RunOverrides.model_validate({"deadlineMode": "hard"})
    assert body.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "deadlineMode": "hard"
    }


def test_baseline_owners_replace_current_owners() -> None:
    """Baseline owners replace current ones; unknown people are ignored."""
    planning_input = make_input()
    rebased = with_baseline(planning_input, {"a": "e2", "b": "former-employee"})
    assert [task.owner_id for task in rebased.tasks] == ["e2", "e1"]
