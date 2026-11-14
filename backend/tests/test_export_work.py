"""Exports state the planned work (round 3, R4).

What: the MS Project export and the PDF give each work package the plan's own dates, its
planned working days and the effort the plan books (weekly rate over those days).
Why: QA's PACK-D-EXPORT-WORK: a package stretched for a part-timer was exported at its WBS
duration, so W09 showed 25.6 h of work instead of 64 h and a one-day 8 h task 4 h.
How it fits: pure functions of app/export on a hand-built run header and assignments.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime

from app.export.model import PlanExport, PlannedRow, RunHeader, build_export
from app.export.msproject import render_msproject
from app.export.pdf import schedule_rows

NAMESPACE = {"project": "http://schemas.microsoft.com/project"}
LONG, SHORT = uuid.uuid4(), uuid.uuid4()


def _plan() -> PlanExport:
    """W09 (8-day, 64 h) stretched to 20 working days at 16 h a week; W01 (1 day, 8 h) by a
    32 h/week person over 2 days at 20 h a week. Planning starts Monday Jan 4, 2027."""
    header = RunHeader(
        id=uuid.uuid4(),
        project_name="EDP",
        status="optimal",
        solver="OR-Tools",
        version=1,
        applied_at=None,
        completion_date=date(2027, 1, 29),
        expected_cost_cents=None,
        result={
            "schedule": [
                {"wbsNodeId": str(LONG), "start": "2027-01-04", "end": "2027-01-29"},
                {"wbsNodeId": str(SHORT), "start": "2027-01-04", "end": "2027-01-05"},
            ]
        },
        snapshot={
            "start_date": "2027-01-04",
            "constraints": {"working_days_per_week": 5},
            "tasks": [
                {"id": str(LONG), "duration_days": 8},
                {"id": str(SHORT), "duration_days": 1},
            ],
        },
        created_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    person = uuid.uuid4()
    rows = [
        PlannedRow(LONG, "1.4.1", "W09", 8, person, "Sara Kim", None, 0, 3, 16.0),
        PlannedRow(SHORT, "1.1.1", "W01", 1, person, "Sara Kim", None, 0, 0, 20.0),
    ]
    return build_export(header, rows)


def test_export_tasks_carry_the_planned_days_and_effort() -> None:
    tasks = {task.name: task for task in _plan().tasks}
    assert (tasks["W09"].start, tasks["W09"].finish) == (date(2027, 1, 4), date(2027, 1, 29))
    assert (tasks["W09"].duration_days, tasks["W09"].work_hours) == (20, 64.0)
    assert (tasks["W01"].duration_days, tasks["W01"].work_hours) == (2, 8.0)


def test_ms_project_work_is_the_planned_effort() -> None:
    root = ET.fromstring(render_msproject(_plan()))  # noqa: S314 (our own export, not input)
    work = {
        task.findtext("project:Name", namespaces=NAMESPACE): (
            task.findtext("project:Duration", namespaces=NAMESPACE),
            task.findtext("project:Work", namespaces=NAMESPACE),
        )
        for task in root.iterfind("project:Tasks/project:Task", NAMESPACE)
    }
    assert work["W09"] == ("PT160H0M0S", "PT64H0M0S")
    assert work["W01"] == ("PT16H0M0S", "PT8H0M0S")
    assigned = [
        assignment.findtext("project:Work", namespaces=NAMESPACE)
        for assignment in root.iterfind("project:Assignments/project:Assignment", NAMESPACE)
    ]
    assert assigned == ["PT64H0M0S", "PT8H0M0S"]


def test_pdf_schedule_agrees_with_ms_project() -> None:
    rows = {row[1]: row for row in schedule_rows(_plan())}
    assert rows["W09"][3:7] == ("Jan 4, 2027", "Jan 29, 2027", "20", "64 h")
    assert rows["W01"][5:7] == ("2", "8 h")
