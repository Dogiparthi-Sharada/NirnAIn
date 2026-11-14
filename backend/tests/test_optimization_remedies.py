"""Remedies for infeasible plans and per-run overrides, on tiny hand-built instances."""

from __future__ import annotations

from datetime import date

from app.domain.optimization import RunOverrides
from app.optimization.model import Constraints, Employee, Milestone, PlanningInput, Task
from app.optimization.overrides import apply_overrides, with_baseline
from app.optimization.remedies import apply_remedy, price_remedies, variants
from app.optimization.solver import SolveOptions, solve
from tests.test_optimization_solver import SAP, make_input, with_constraints

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
    inp = _sap_gap_input()
    assert solve(inp, TINY).status == "infeasible"

    remedies = price_remedies(inp, TINY)

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
    inp = with_constraints(make_input(), must_finish_by=date(2026, 10, 16), deadline_mode="hard")
    assert solve(inp, TINY).status == "infeasible"

    (remedy,) = price_remedies(inp, TINY)

    assert remedy.id == "move_deadline"
    assert remedy.title == "Move go-live to Oct 23 (+7 days)"
    assert remedy.completion_date == date(2026, 10, 23)
    assert remedy.detail == "Go-live Oct 23, 2026 · no contractor · about $12,000"


def test_hard_milestone_is_relaxed_with_the_deadline() -> None:
    inp = make_input(
        milestones=(Milestone("m", "Design done", date(2026, 10, 6), "hard", ("a",)),),
    )
    assert [remedy_id for remedy_id, _ in variants(inp, solved=False)] == [
        "specialist",
        "move_deadline",
    ]
    (remedy,) = price_remedies(inp, TINY)
    assert remedy.title == "Let the phase milestones slip"
    assert remedy.detail.endswith("phase milestones may slip (soft)")


def test_no_remedy_when_nothing_relaxable() -> None:
    inp = with_constraints(make_input(), budget_total_cents=1, budget_mode="hard")
    # A contractor is always tried, but costs more: still over the cap.
    assert [remedy_id for remedy_id, _ in variants(inp, solved=False)] == ["specialist"]
    assert price_remedies(inp, TINY) == []


def test_run_with_remedy_applies_it() -> None:
    inp = _sap_gap_input()
    relaxed = apply_remedy(inp, "specialist")
    assert relaxed.constraints.allow_contractors is True
    assert relaxed.constraints.contractor_rate_cents == 17_500
    assert solve(relaxed, TINY).status in ("optimal", "feasible")
    overtime = apply_remedy(with_constraints(inp, capacity_mode="hard"), "overtime")
    assert overtime.constraints.capacity_mode == "soft"
    assert overtime.constraints.target_penalty_per_day > 0  # it tries to keep the date

    with_milestone = make_input(milestones=(Milestone("m", "M", date(2026, 10, 6)),))
    moved = apply_remedy(with_milestone, "move_deadline")
    assert moved.constraints.deadline_mode == "soft"
    assert [milestone.mode for milestone in moved.milestones] == ["soft"]
    assert apply_remedy(inp, None) is inp


# --------------------------------------------------------------------------- overrides
def test_overrides_change_only_this_run() -> None:
    inp = make_input(
        tasks=(
            Task("a", "1.1.1", "Design", 5, effort_hours=20, owner_id="e1"),
            Task("b", "1.1.2", "Build", 10, owner_id="e1"),
        )
    )
    changed = apply_overrides(
        inp,
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
    assert inp.constraints.budget_total_cents is None  # the original is untouched
    assert apply_overrides(inp, None) is inp
    assert apply_overrides(inp, {}) is inp


def test_overrides_set_the_deadline_and_budget_modes() -> None:
    inp = with_constraints(make_input(), deadline_mode="soft", budget_mode="hard")
    changed = apply_overrides(inp, {"deadlineMode": "hard", "budgetMode": "soft"})
    assert (changed.constraints.deadline_mode, changed.constraints.budget_mode) == (
        "hard",
        "soft",
    )
    kept = apply_overrides(inp, {"mustFinishBy": "2026-12-18"})
    assert (kept.constraints.deadline_mode, kept.constraints.budget_mode) == ("soft", "hard")
    body = RunOverrides.model_validate({"deadlineMode": "hard"})
    assert body.model_dump(mode="json", by_alias=True, exclude_none=True) == {
        "deadlineMode": "hard"
    }


def test_baseline_owners_replace_current_owners() -> None:
    inp = make_input()
    rebased = with_baseline(inp, {"a": "e2", "b": "former-employee"})
    assert [task.owner_id for task in rebased.tasks] == ["e2", "e1"]
