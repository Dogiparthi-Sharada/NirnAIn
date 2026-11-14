"""Re-plan overrides that change the team: people leaving and people joining mid-project.

What: `apply_overrides` with `removeMembers` / `addMembers`, on a pure planning input, and
one small CP-SAT solve proving the solver reassigns a leaver's work to the newcomer.
Why: step 7 lets the PM bring in a specialist or take someone off the project after Plan v1;
the re-plan must plan with exactly that team. How it fits: covers app/optimization/overrides.py
(C1's run endpoint applies the overrides before freezing the snapshot).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.changes import AddedMember, PlanOverrides
from app.domain.optimization import RunCreate, RunOverrides
from app.optimization.model import (
    DEFAULT_HOURLY_RATE_CENTS,
    Allocation,
    Employee,
    PlanningInput,
    SkillRequirement,
    Task,
)
from app.optimization.overrides import NEW_MEMBER_PROFICIENCY, apply_overrides
from app.optimization.resources import build_resources
from app.optimization.solver import SolveOptions, solve

MONDAY = date(2026, 10, 5)
REVIEW = SkillRequirement("skill-review", "Security & Compliance Review", 3)
NEWCOMER_ID = "5b0c1f9e-2f7a-4d55-9a53-3c9a3d2b6f10"


def _input() -> PlanningInput:
    """Sam owns the compliance review; Mark builds. Both are booked elsewhere too."""
    return PlanningInput(
        project_id="p1",
        project_name="EDP",
        start_date=MONDAY,
        tasks=(
            Task("review", "1.1.1", "Compliance review", 5, owner_id="sam", skills=(REVIEW,)),
            Task("build", "1.1.2", "Build", 5, owner_id="mark"),
        ),
        employees=(
            Employee("mark", "Mark Chen", 40, hourly_rate_cents=10_000),
            Employee(
                "sam", "Sam Okafor", 16, hourly_rate_cents=12_000, skills={REVIEW.skill_id: 5}
            ),
        ),
        allocations=(Allocation("sam", MONDAY, 8), Allocation("mark", MONDAY, 4)),
    )


def _dana(**fields: Any) -> dict[str, Any]:
    """Dana Lee as she arrives on the wire (camelCase, as stored in the run's options)."""
    return {
        "employeeId": NEWCOMER_ID,
        "fullName": "Dana Lee",
        "roleTitle": "Security Specialist",
        "hoursPerWeek": 24,
        "skills": ["security & compliance review", "Pen testing"],
        "isContractor": True,
        **fields,
    }


def test_leaver_drops_out_with_their_ownership_and_bookings() -> None:
    changed = apply_overrides(_input(), {"removeMembers": ["sam"], "hoursPerWeek": {"sam": 8}})
    assert [employee.id for employee in changed.employees] == ["mark"]
    # Their package has no current owner: the solver places it without a move penalty.
    assert {task.id: task.owner_id for task in changed.tasks} == {"review": None, "build": "mark"}
    assert [booking.employee_id for booking in changed.allocations] == ["mark"]


def test_newcomer_joins_with_matched_skills_and_the_team_rate() -> None:
    changed = apply_overrides(_input(), {"addMembers": [_dana()]})
    dana = changed.employees[-1]
    assert (dana.id, dana.name, dana.weekly_capacity_hours, dana.is_contractor) == (
        NEWCOMER_ID,
        "Dana Lee",
        24.0,
        True,
    )
    # Skills match the work packages' requirements by name; unknown ones don't matter.
    assert dict(dana.skills) == {REVIEW.skill_id: NEW_MEMBER_PROFICIENCY}
    # No rate given: null in the input; the optimizer prices it at the named default rate.
    assert dana.hourly_rate_cents is None
    resources = {resource.id: resource for resource in build_resources(changed)}
    assert resources[NEWCOMER_ID].rate_cents == DEFAULT_HOURLY_RATE_CENTS
    assert PlanningInput.from_snapshot(changed.to_snapshot()) == changed

    priced = apply_overrides(_input(), {"addMembers": [_dana(hourlyRateCents=17_500)]})
    assert priced.employees[-1].hourly_rate_cents == 17_500
    # Someone already in the input isn't added twice.
    again = apply_overrides(changed, {"addMembers": [_dana()]})
    assert [employee.id for employee in again.employees] == ["mark", "sam", NEWCOMER_ID]


def test_solver_reassigns_the_leavers_work_to_the_newcomer() -> None:
    changed = apply_overrides(
        _input(), {"removeMembers": ["sam"], "addMembers": [_dana(hourlyRateCents=17_500)]}
    )
    outcome = solve(changed, SolveOptions(time_limit_s=10, workers=1), explain=False)
    assert outcome.status in ("optimal", "feasible")
    assert outcome.plan is not None
    # Only Dana has the review skill now; nothing is planned for Sam.
    assert outcome.plan.tasks["review"].resource_id == NEWCOMER_ID
    assert all(task.resource_id != "sam" for task in outcome.plan.tasks.values())


def test_a_changes_overrides_are_a_valid_run_body() -> None:
    """Step 7 posts `change.overrides` to the run endpoint as is; it must validate and
    reach the solver input exactly as stored in the run's options."""
    member = {
        key: value for key, value in _dana(hourlyRateCents=None).items() if key != "employeeId"
    }
    overrides = PlanOverrides(
        add_members=[AddedMember.model_validate({**member, "employeeId": NEWCOMER_ID})],
        remove_members=[uuid.UUID(int=7)],
    )
    body = RunCreate.model_validate({"overrides": overrides.model_dump(mode="json")})
    stored = body.model_dump(mode="json", by_alias=True, exclude_none=True)["overrides"]
    changed = apply_overrides(_input(), stored)
    assert [employee.name for employee in changed.employees] == [
        "Mark Chen",
        "Sam Okafor",
        "Dana Lee",
    ]
    # A newcomer the run can't name (no reserved employee) is refused at the boundary.
    with pytest.raises(ValidationError):
        RunOverrides.model_validate({"addMembers": [member]})
