"""Tests of the per-project dashboard overview: the derivations, the overview and the endpoint.

Purpose: The overview must never invent numbers: KPIs, peaks, risks and the next step come from the
applied plan and the project's progress, or are left out.
How it fits: Pure tests of app/domain/overview.py and the endpoint with a fake repository, on the
data of tests/support/overview.py; layer: tests.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import ConstraintMode, NotificationLevel, ScopeStatus
from app.domain.notifications import Notification
from app.domain.overview import (
    build_overview,
    constraint_choices,
    next_step,
    peak_utilization,
    soft_violations,
)
from app.domain.overview_facts import (
    OverviewFacts,
    PolicyFacts,
    ProgressFacts,
    ScopeRisk,
)
from app.domain.overview_milestones import MilestoneFact
from app.domain.projects import Project
from tests.support.overview import ANSWERED, READY, RESULT, TODAY, edp_project, plan_v2_run
from tests.support.store import Store


# --------------------------------------------------------------------------- derivations
def test_peak_utilization_and_soft_violations_read_the_run_result() -> None:
    """The peak and soft violations come from the run's summary, never from averages."""
    assert peak_utilization(RESULT) == 125
    assert peak_utilization({"utilization": RESULT["utilization"]}) is None  # no averages
    assert peak_utilization({"summary": {"peakUtilization": None}}) is None
    assert peak_utilization(None) is None
    assert soft_violations(RESULT) == 0
    assert soft_violations({"violations": {"soft": 2}}) == 2
    assert soft_violations({"softViolations": [{"name": "Phase 1"}]}) == 1


def test_constraint_choices_deadline_follows_the_answer() -> None:
    """The constraint choices follow the modes, the deadline one the answer."""
    assert [(choice.key, choice.label) for choice in constraint_choices(None)] == [
        ("budget", "Must hold"),
        ("deadline", "Must hold"),
        ("capacity", "Can flex"),
        ("skills", "Must hold"),
    ]
    flexed = constraint_choices(
        PolicyFacts(budget_mode=ConstraintMode.SOFT, deadline_answer="soft")
    )
    assert flexed[0].mode == ConstraintMode.SOFT
    assert flexed[1].label == "Can flex"
    assert constraint_choices(PolicyFacts(deadline_answer="unknown"))[1].mode == "hard"


def test_next_step_follows_the_workflow() -> None:
    """The next step follows the workflow from intake to changes."""
    assert next_step(ProgressFacts(), None, False).step == "intake"
    scope = next_step(ProgressFacts(scope_items=4, pending_scope_items=1), None, False)
    assert (scope.step, scope.label) == ("scope", "Review 1 scope item AI extracted")
    assert next_step(ProgressFacts(scope_items=4), None, False).step == "wbs"
    todo = next_step(
        ProgressFacts(scope_items=4, wbs_nodes=6, unconfirmed_ai_estimates=3), None, False
    )
    assert todo.step == "constraints"
    assert todo.label == "Set the constraints: confirm 3 AI estimates and answer 2 questions"
    assert next_step(READY, PolicyFacts(deadline_answer="soft"), False).label.endswith(
        "answer 1 question"
    )
    assert next_step(READY, ANSWERED, False).label == "Run the optimizer and apply a plan"
    solved = ProgressFacts(scope_items=5, wbs_nodes=8, has_successful_run=True)
    assert next_step(solved, ANSWERED, False).step == "optimization"
    assert next_step(solved, ANSWERED, True).step == "changes"


def test_build_overview_without_a_plan_never_invents_numbers() -> None:
    """Without a plan there are no plan numbers; risks come from the scope."""
    store = Store()
    project = edp_project(store, store.add_user())
    overview = build_overview(
        OverviewFacts(
            project=project,
            scope_risks=[
                ScopeRisk("SAP timeline uncertain", ScopeStatus.ACCEPTED),
                ScopeRisk("Vendor lock-in", ScopeStatus.PENDING),
            ],
        ),
        TODAY,
    )
    body = overview.model_dump(mode="json")
    assert body["appliedVersion"] is None
    assert body["plan"] is None
    assert body["spentToDateCents"] is None
    assert body["project"]["dueDate"] == "2026-09-30"
    assert body["project"]["pmName"] == "Jane Doe"
    assert [risk["level"] for risk in body["risks"]] == ["Medium", "Medium"]
    assert body["risks"][1]["source"] == "From AI intake, not reviewed yet"
    assert body["nextStep"] == {"label": "Run AI Project Intake on your notes", "step": "intake"}


def test_build_overview_with_an_applied_plan() -> None:
    """With a plan the overview shows its version, peak, KPIs, risks and findings."""
    store = Store()
    project = edp_project(store, store.add_user())
    overview = build_overview(
        OverviewFacts(
            project=project,
            policy=ANSWERED,
            progress=READY,
            milestones=[MilestoneFact("Phase 1", date(2026, 10, 1))],
            applied_run=plan_v2_run(),
            validation_issues=["a", "b", "c", "d"],
            recommendation_reasons=["Sara Kim: Skill gap"],
        ),
        TODAY,
    )
    body = overview.model_dump(mode="json")
    plan = body["plan"]
    assert body["appliedVersion"] == 2
    assert plan["version"] == 2
    assert plan["peakUtilization"] == 125
    assert plan["violations"] == {"hard": 0, "soft": 0}
    assert [kpi["label"] for kpi in plan["kpis"]] == ["Go-live"]
    assert body["risks"] == [
        {
            "level": "High",
            "text": "Sara Kim is booked at 110% in Plan v2",
            "source": "From the plan",
        }
    ]
    assert body["milestones"]["next"]["status"] == "On track"
    assert [finding["source"] for finding in body["findings"]] == ["wbs_validate"] * 3 + [
        "optimization"
    ]
    assert body["nextStep"]["step"] == "changes"


# --------------------------------------------------------------------------- endpoint
class FakeOverviews:
    """Overview repository that returns fixed facts with a notification."""

    def __init__(self, user_id: uuid.UUID) -> None:
        """Act as `user_id`."""
        self.user_id = user_id

    async def facts(self, project: Project, user_id: uuid.UUID) -> OverviewFacts:
        """Return Plan v2's facts with one notification of twelve."""
        assert user_id == self.user_id
        note = Notification(
            id=uuid.uuid4(),
            org_id=project.org_id,
            project_id=project.id,
            level=NotificationLevel.INFO,
            message="Plan v2 applied",
            read_at=None,
            created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        return OverviewFacts(
            project=project,
            policy=ANSWERED,
            progress=READY,
            applied_run=plan_v2_run(),
            notifications=[note],
            notification_total=12,
        )


def test_overview_endpoint(
    app: FastAPI,
    client: TestClient,
    store: Store,
    auth: Callable[[uuid.UUID], dict[str, str]],
) -> None:
    """The endpoint returns the overview to members and 404 problem+json to others."""

    def overviews(principal: CurrentPrincipal) -> FakeOverviews:
        """Return the fake repository acting as the caller."""
        return FakeOverviews(principal.user_id)

    app.dependency_overrides[deps.get_overview_repo] = overviews
    owner = store.add_user()
    project = edp_project(store, owner)

    response = client.get(f"/api/v1/projects/{project.id}/overview", headers=auth(owner))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["appliedVersion"] == 2
    assert body["notificationTotal"] == 12
    assert body["notifications"][0]["message"] == "Plan v2 applied"
    assert body["constraintChoices"][1] == {
        "key": "deadline",
        "name": "Deadline",
        "mode": "hard",
        "label": "Must hold",
    }

    stranger = store.add_user()
    hidden = client.get(f"/api/v1/projects/{project.id}/overview", headers=auth(stranger))
    assert hidden.status_code == 404
    assert hidden.headers["content-type"].startswith("application/problem+json")
