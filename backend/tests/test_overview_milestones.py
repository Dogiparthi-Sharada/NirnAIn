"""Tests of how the overview judges milestones against the applied plan.

Purpose: A milestone is on track, at risk or not planned yet by the packages under its WBS node (QA
pack VIEW-001), not by the whole project's finish; one without a node uses the completion.
How it fits: Pure tests of app/domain/overview_milestones.py and the milestone query of
app/repositories/overview.py on a recording connection; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from app.domain.overview import (
    build_overview,
)
from app.domain.overview_facts import (
    OverviewFacts,
)
from app.domain.overview_milestones import MilestoneFact, milestone_summary, planned_finishes
from app.repositories.overview import OverviewRepository
from tests.support.connections import Conn, Rows
from tests.support.overview import TODAY, edp_project, plan_v2_run
from tests.support.store import Store


def test_a_milestone_without_a_wbs_node_compares_with_the_plan_completion() -> None:
    """A milestone without a WBS node is compared with the plan's completion."""
    milestones = [
        MilestoneFact("Kickoff", date(2026, 2, 1)),
        MilestoneFact("Phase 2", date(2026, 9, 30)),
        MilestoneFact("Phase 1", date(2026, 4, 15)),
    ]
    unplanned = milestone_summary(milestones, None, {}, TODAY)
    assert unplanned.total == 3
    assert unplanned.next is not None
    assert (unplanned.next.name, unplanned.next.status) == ("Phase 1", "Not planned yet")
    assert milestone_summary(milestones, date(2026, 4, 15), {}, TODAY).next.status == "On track"  # type: ignore[union-attr]
    assert milestone_summary(milestones, date(2026, 5, 1), {}, TODAY).next.status == "At risk"  # type: ignore[union-attr]
    assert milestone_summary(milestones[:1], None, {}, TODAY).next is None


def _schedule(*ends: tuple[str, str]) -> dict[str, Any]:
    """Return a run result scheduling each node to end on the given date."""
    return {"schedule": [{"wbsNodeId": node_id, "end": end} for node_id, end in ends]}


def test_a_milestone_is_judged_by_its_own_packages_not_the_project_finish() -> None:
    """QA pack VIEW-001: MS1 'Ingestion (W01-W03)' is due Apr 30, 2027; Plan v1 finishes
    W01-W03 by Feb 26 and the whole project on Jun 23. MS1 is on track."""
    ingestion = MilestoneFact(
        "MS1 Ingestion (W01-W03)", date(2027, 4, 30), frozenset({"d1", "w1", "w3"})
    )
    plan_v1 = _schedule(("w1", "2027-01-29"), ("w3", "2027-02-26"), ("w9", "2027-06-23"))
    finishes = planned_finishes(plan_v1)
    summary = milestone_summary([ingestion], date(2027, 6, 23), finishes, date(2026, 11, 14))
    assert summary.next is not None
    assert summary.next.status == "On track"
    # One of its own packages after its date puts it at risk, however early the rest is.
    late = planned_finishes(_schedule(("w1", "2027-01-29"), ("w3", "2027-05-03")))
    assert milestone_summary([ingestion], date(2027, 5, 3), late, TODAY).next.status == "At risk"  # type: ignore[union-attr]
    # A plan that schedules none of its packages hasn't planned it.
    other = planned_finishes(_schedule(("w9", "2027-06-23"), ("bad", "not a date")))
    assert other == {"w9": date(2027, 6, 23)}
    assert milestone_summary([ingestion], date(2027, 6, 23), other, TODAY).next.status == (  # type: ignore[union-attr]
        "Not planned yet"
    )
    applied = build_overview(
        OverviewFacts(
            project=edp_project(Store(), uuid.uuid4()),
            milestones=[ingestion],
            applied_run=plan_v2_run(plan_v1, completion=date(2027, 6, 23)),
        ),
        date(2026, 11, 14),
    )
    assert applied.milestones.next is not None
    assert applied.milestones.next.status == "On track"


async def test_milestones_cover_their_wbs_node_and_everything_under_it() -> None:
    """A milestone covers its WBS node and every node under it."""
    root, deliverable, first_package, second_package, other = (uuid.uuid4() for _ in range(5))
    conn = Conn(
        Rows(
            [
                {"name": "MS1", "due_date": date(2027, 4, 30), "wbs_node_id": deliverable},
                {"name": "Go-live", "due_date": date(2027, 7, 30), "wbs_node_id": None},
            ]
        ),
        Rows(
            [
                {"id": root, "parent_id": None},
                {"id": deliverable, "parent_id": root},
                {"id": first_package, "parent_id": deliverable},
                {"id": second_package, "parent_id": deliverable},
                {"id": other, "parent_id": root},
            ]
        ),
    )
    found = await OverviewRepository(conn)._milestones(uuid.uuid4())  # type: ignore[arg-type]
    assert found == [
        MilestoneFact(
            "MS1",
            date(2027, 4, 30),
            frozenset({str(deliverable), str(first_package), str(second_package)}),
        ),
        MilestoneFact("Go-live", date(2027, 7, 30)),
    ]
