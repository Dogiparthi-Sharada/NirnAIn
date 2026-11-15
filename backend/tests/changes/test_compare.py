"""Tests of the plan comparison shown before a change is approved.

Purpose: Prove each work package's move is measured in the days each plan schedules and comes with
its reason (done, effort, follows another package).
How it fits: Pure tests of app/changes/compare.py with the impact from app/changes/derive.py;
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date

from app.changes.compare import build_comparison, schedule_dates
from app.changes.context import NodeRow, PlanRow, RunMeta
from app.changes.derive import build_impact
from app.changes.detect import detect
from app.domain.common import SolverStatus
from tests.support.changes import (
    ETL,
    MARK,
    NOTES,
    SALESFORCE,
    SAP,
    project_context,
)


# --------------------------------------------------------------------------- compare
def test_comparison_shows_what_moved_and_why() -> None:
    """Done work leaves the plan; changed effort and its successors shift with their reasons."""
    context = project_context()
    impact = build_impact(detect(NOTES, context), "notes", context, "rules")
    snapshot = {
        "start_date": "2025-01-13",
        "constraints": {"budget_total_cents": 37_500_000, "working_days_per_week": 5},
        "dependencies": [{"predecessor_id": str(SAP), "successor_id": str(ETL)}],
    }
    base = RunMeta(uuid.uuid4(), uuid.uuid4(), SolverStatus.OPTIMAL, 1, 1, None, snapshot)
    proposed = RunMeta(uuid.uuid4(), base.project_id, SolverStatus.OPTIMAL, None, 2, None, snapshot)
    root, group = uuid.uuid4(), uuid.uuid4()
    nodes = [
        NodeRow(root, None, 1, "1", "Platform"),
        NodeRow(group, root, 2, "1.1", "Ingestion"),
        NodeRow(SALESFORCE, group, 3, "1.1.1", "Salesforce connector development"),
        NodeRow(SAP, group, 3, "1.1.2", "SAP integration setup"),
        NodeRow(ETL, group, 3, "1.2.2", "ETL pipeline development"),
    ]
    before = [
        PlanRow(SALESFORCE, MARK, None, 0, 2),
        PlanRow(SAP, None, "SAP specialist", 3, 6),
        PlanRow(ETL, MARK, None, 7, 11),
    ]
    after = [PlanRow(SAP, None, "SAP specialist", 3, 8), PlanRow(ETL, MARK, None, 9, 13)]
    comparison = build_comparison(base, before, proposed, after, nodes, {MARK: "Mark Chen"}, impact)
    rows = {row.code: row for row in comparison.rows}
    assert rows["1.1.1"].after is None
    assert rows["1.1.1"].why == "Done, nothing left to plan"
    assert (rows["1.1.2"].shift_days, rows["1.1.2"].why) == (14, "Effort 4w → 6w")
    assert (rows["1.2.2"].shift_days, rows["1.2.2"].why) == (14, "Follows 1.1.2")
    assert rows["1.2.2"].group_code == "1.1"
    assert rows["1.1.2"].before is not None
    assert rows["1.1.2"].before.start == date(2025, 2, 3)
    assert comparison.base.budget_cents == 37_500_000


def test_comparison_uses_the_days_each_plan_schedules() -> None:
    """QA pack CHANGE-009: ETL moves one day (Jan 11 → Jan 12), not a whole week."""
    snapshot = {"start_date": "2027-01-04", "constraints": {"working_days_per_week": 5}}

    def meta(ends: dict[uuid.UUID, str]) -> RunMeta:
        """Return run metadata whose schedule ends each node on the given date."""
        schedule = schedule_dates(
            [
                {"wbsNodeId": str(node), "start": "2027-01-04", "end": end}
                for node, end in ends.items()
            ]
            + [{"wbsNodeId": "not-a-uuid", "start": "2027-01-04", "end": "2027-01-04"}]
        )
        return RunMeta(
            uuid.uuid4(), uuid.uuid4(), SolverStatus.OPTIMAL, 1, 1, None, snapshot, schedule
        )

    base = meta({SAP: "2027-01-05", ETL: "2027-01-11"})
    proposed = meta({SAP: "2027-01-05", ETL: "2027-01-12"})
    rows = [PlanRow(SAP, MARK, None, 0, 0), PlanRow(ETL, MARK, None, 1, 1)]
    nodes = [
        NodeRow(SAP, None, 2, "1.1", "SAP integration setup"),
        NodeRow(ETL, None, 2, "1.2", "ETL"),
    ]
    comparison = build_comparison(base, rows, proposed, rows, nodes, {MARK: "Mark Chen"}, None)
    by_code = {row.code: row for row in comparison.rows}
    assert by_code["1.1"].before is not None
    assert (by_code["1.1"].before.start, by_code["1.1"].before.end) == (
        date(2027, 1, 4),
        date(2027, 1, 5),
    )
    assert by_code["1.1"].shift_days == 0
    assert by_code["1.2"].shift_days == 1
