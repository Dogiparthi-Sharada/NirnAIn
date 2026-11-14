"""Hours booked on other projects reduce a person's free hours (QA pack RES-006).

Applying a plan books its hours (`allocations`); another project's run sees
them as load the person already carries (`load.allocation_blocks`). Those
hours come off the person's daily capacity, and what is left stays usable:
a task may stretch to the hours left beside a booking (`load.booked_modes`)
instead of only waiting for the booking to end.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.optimization.explain import build_report
from app.optimization.load import Block, Mode, booked_modes
from app.optimization.model import (
    Allocation,
    ConstraintMode,
    Constraints,
    Employee,
    PlanningInput,
    Task,
)
from app.optimization.solver import solve
from tests.support.solver_inputs import FAST, make_input

JAN_4 = date(2027, 1, 4)  # a Monday
JAN_8 = date(2027, 1, 8)


def _project_y(effort_hours: float, capacity_mode: ConstraintMode) -> PlanningInput:
    """Pat gives project Y 40 h/week and is booked 16 h elsewhere in the week of Jan 4:
    24 h are free. One package of `effort_hours` (3 working days in the WBS) is due
    Friday Jan 8, and the deadline must hold."""
    return make_input(
        start_date=JAN_4,
        tasks=(Task("y", "1.1.1", "Y work", 3, effort_hours=effort_hours, owner_id="pat"),),
        employees=(Employee("pat", "Pat Shared", hourly_rate_cents=8_500),),
        dependencies=(),
        allocations=(Allocation("pat", JAN_4, 16),),
        constraints=Constraints(
            must_finish_by=JAN_8, allow_contractors=False, capacity_mode=capacity_mode
        ),
    )


@pytest.mark.parametrize("capacity_mode", ["hard", "soft"])
def test_work_that_fits_the_hours_left_beside_a_booking_is_planned(
    capacity_mode: ConstraintMode,
) -> None:
    inp = _project_y(24, capacity_mode)
    outcome = solve(inp, FAST)
    assert outcome.status == "optimal"
    assert outcome.plan is not None
    scheduled = outcome.plan.tasks["y"]
    # 24 h at the 4.8 h/day left (8 h less 16 h / 5 days): the whole week.
    assert (scheduled.start, scheduled.end) == (0, 5)
    report = build_report(inp, outcome)
    assert report.result is not None
    summary = report.result["summary"]
    assert summary["completionDate"] == JAN_8.isoformat()
    assert summary["softViolations"] == []  # no overtime: 4.8 h + 3.2 h booked = 8 h a day
    assert summary["peakUtilization"] == {"name": "Pat Shared", "percent": 100}
    assert report.result["scheduleChanges"][0]["reason"] == (
        "Pat Shared at 40h/week less other projects' bookings: 5 working days instead of 3"
    )


def test_more_work_than_the_hours_left_is_still_refused() -> None:
    outcome = solve(_project_y(32, "hard"), FAST)
    assert outcome.status == "infeasible"
    assert {conflict.name for conflict in outcome.conflicts} >= {
        "Deadline: must finish by Jan 8, 2027"
    }


def test_booked_modes_stretch_a_task_to_each_level_of_hours_left() -> None:
    task = Task("y", "1.1.1", "Y work", 3, effort_hours=24)
    light, heavy = Block(0, 5, 16), Block(5, 10, 32)
    # 8 h/day capacity: 6.4 h and 4.8 h are left beside the two bookings.
    assert booked_modes(task, 80, [light, heavy]) == [
        Mode(4, 60, stretched=True),
        Mode(5, 48, stretched=True),
    ]
    # A booking that leaves the full daily demand, or nothing at all, adds no mode.
    assert booked_modes(task, 80, [Block(0, 5, 0), Block(5, 10, 80)]) == []
    assert booked_modes(task, None, [heavy]) == []
