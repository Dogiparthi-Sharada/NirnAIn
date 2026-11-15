"""Milestone status on the per-project dashboard (QA pack VIEW-001).

Purpose: A milestone is checked against its own work as the plan in force schedules it: a milestone
on a WBS node is on track when the last package under that node finishes by its date; one without a
node follows the plan's completion; without a plan it is "Not planned yet".
How it fits: Called by app/domain/overview.py; app/repositories/overview.py builds the
`MilestoneFact`s; layer: domain.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from app.domain.common import CamelModel

MilestoneStatus = Literal["On track", "At risk", "Not planned yet"]


@dataclass(frozen=True, slots=True)
class MilestoneFact:
    """A milestone and the WBS nodes it covers: its own node and everything under it
    (none when it isn't on a WBS node)."""

    name: str
    due_date: date
    node_ids: frozenset[str] = frozenset()


class NextMilestone(CamelModel):
    """The next milestone due, and its status in the plan in force."""

    name: str
    due_date: date
    status: MilestoneStatus


class MilestoneSummary(CamelModel):
    """The dashboard's milestone figures: how many there are, and the next one due."""

    total: int
    next: NextMilestone | None


def planned_finishes(result: Mapping[str, Any] | None) -> dict[str, date]:
    """Work package id -> the last day the plan gives it (`result.schedule`)."""
    rows = (result or {}).get("schedule")
    finishes: dict[str, date] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("end"), str):
            continue
        try:
            finishes[str(row.get("wbsNodeId"))] = date.fromisoformat(row["end"])
        except ValueError:
            continue
    return finishes


def milestone_status(
    milestone: MilestoneFact, completion: date | None, finishes: Mapping[str, date]
) -> MilestoneStatus:
    """Return a milestone's status in the plan in force.

    On a WBS node: "On track" when the last package under it finishes by the due date (finished work
    counts at the dates it was done), else "At risk", whatever the rest of the project does. Without
    a node the plan's completion decides; with no plan or none of its packages planned, "Not planned
    yet".
    """
    if completion is None:
        return "Not planned yet"
    if milestone.node_ids:
        own = [finishes[node_id] for node_id in milestone.node_ids if node_id in finishes]
        if not own:
            return "Not planned yet"
        finish = max(own)
    else:
        finish = completion
    return "On track" if finish <= milestone.due_date else "At risk"


def milestone_summary(
    milestones: Sequence[MilestoneFact],
    completion: date | None,
    finishes: Mapping[str, date],
    today: date,
) -> MilestoneSummary:
    """How many milestones there are, and the next one due with its status in the plan
    in force (`completion` None: no plan in force)."""
    upcoming = sorted(
        (milestone for milestone in milestones if milestone.due_date >= today),
        key=lambda milestone: milestone.due_date,
    )
    if not upcoming:
        return MilestoneSummary(total=len(milestones), next=None)
    first = upcoming[0]
    return MilestoneSummary(
        total=len(milestones),
        next=NextMilestone(
            name=first.name,
            due_date=first.due_date,
            status=milestone_status(first, completion, finishes),
        ),
    )
