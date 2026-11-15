"""Shared data for the overview tests: the project, Plan v2's run and the answered policy.

Purpose: The overview derivations, milestones and endpoint tests judge one Enterprise Data Platform
project against the same applied plan and the same day.
How it fits: Used by tests/test_overview.py and test_overview_milestones.py to build
app/domain/overview_facts.py inputs; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from app.domain.overview_facts import (
    AppliedRunFacts,
    PolicyFacts,
    ProgressFacts,
)
from app.domain.projects import Project
from tests.support.store import Store

TODAY = date(2026, 3, 2)


RESULT: dict[str, Any] = {
    "kpis": [
        {"label": "Go-live", "value": "Sep 19, 2026", "sub": "11 days early"},
        {"label": "broken"},
    ],
    "utilization": [
        {"name": "Mark Chen", "before": 100, "after": 75},
        {"name": "Sara Kim", "before": 90, "after": 110},
        {"name": "Contractor", "before": 0, "after": "n/a"},
    ],
    # Sara's busiest day, not her 110% average over the plan (QA pack RES-021).
    "summary": {"peakUtilization": {"name": "Sara Kim", "percent": 125}},
}


def plan_v2_run(
    result: dict[str, Any] | None = None, completion: date | None = None
) -> AppliedRunFacts:
    """Return an applied Plan v2 with the given result and completion date."""
    return AppliedRunFacts(
        run_id=uuid.uuid4(),
        version=2,
        applied_at=datetime(2026, 3, 1, tzinfo=UTC),
        expected_cost_cents=32_060_000,
        completion_date=completion or date(2026, 9, 19),
        result=RESULT if result is None else result,
    )


ANSWERED = PolicyFacts(deadline_answer="hard", specialist_answer="no")


READY = ProgressFacts(scope_items=5, wbs_nodes=8)


def edp_project(store: Store, owner: uuid.UUID) -> Project:
    """Add the Enterprise Data Platform project of a new organization to the store."""
    org = store.add_org(owner)
    now = datetime(2026, 1, 5, tzinfo=UTC)
    project = Project.model_validate(
        {
            "id": uuid.uuid4(),
            "org_id": org,
            "code": "PRJ-1042",
            "name": "Enterprise Data Platform",
            "description": None,
            "type": "technology",
            "department": None,
            "sponsor": None,
            "pm_user_id": owner,
            "pm_name": "Jane Doe",
            "priority": "high",
            "methodology": "waterfall",
            "visibility": "internal",
            "start_date": date(2026, 1, 5),
            "target_end_date": date(2026, 9, 30),
            "budget_cents": 37_500_000,
            "currency": "USD",
            "status": "on_track",
            "current_step": "done",
            "progress_pct": 8,
            "created_by": owner,
            "created_at": now,
            "updated_at": now,
            "archived_at": None,
        }
    )
    store.projects[project.id] = project
    return project
