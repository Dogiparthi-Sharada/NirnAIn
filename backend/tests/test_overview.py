"""Per-project dashboard overview: pure derivations and the endpoint (with fakes)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import CurrentPrincipal
from app.domain.common import ConstraintMode, NotificationLevel, ScopeStatus
from app.domain.notifications import Notification
from app.domain.overview import (
    AppliedRunFacts,
    OverviewFacts,
    PolicyFacts,
    ProgressFacts,
    ScopeRisk,
    build_overview,
    constraint_choices,
    next_step,
    peak_utilization,
    soft_violations,
)
from app.domain.overview_milestones import MilestoneFact, milestone_summary, planned_finishes
from app.domain.projects import Project
from app.repositories.overview import OverviewRepository
from tests.conftest import Store
from tests.test_optimization_repo import Rows
from tests.test_repositories import Conn

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


def _run(result: dict[str, Any] | None = None, completion: date | None = None) -> AppliedRunFacts:
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


# --------------------------------------------------------------------------- derivations
def test_peak_utilization_and_soft_violations_read_the_run_result() -> None:
    assert peak_utilization(RESULT) == 125
    assert peak_utilization({"utilization": RESULT["utilization"]}) is None  # no averages
    assert peak_utilization({"summary": {"peakUtilization": None}}) is None
    assert peak_utilization(None) is None
    assert soft_violations(RESULT) == 0
    assert soft_violations({"violations": {"soft": 2}}) == 2
    assert soft_violations({"softViolations": [{"name": "Phase 1"}]}) == 1


def test_constraint_choices_deadline_follows_the_answer() -> None:
    assert [(c.key, c.label) for c in constraint_choices(None)] == [
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


def test_a_milestone_without_a_wbs_node_compares_with_the_plan_completion() -> None:
    ms = [
        MilestoneFact("Kickoff", date(2026, 2, 1)),
        MilestoneFact("Phase 2", date(2026, 9, 30)),
        MilestoneFact("Phase 1", date(2026, 4, 15)),
    ]
    unplanned = milestone_summary(ms, None, {}, TODAY)
    assert unplanned.total == 3
    assert unplanned.next is not None
    assert (unplanned.next.name, unplanned.next.status) == ("Phase 1", "Not planned yet")
    assert milestone_summary(ms, date(2026, 4, 15), {}, TODAY).next.status == "On track"  # type: ignore[union-attr]
    assert milestone_summary(ms, date(2026, 5, 1), {}, TODAY).next.status == "At risk"  # type: ignore[union-attr]
    assert milestone_summary(ms[:1], None, {}, TODAY).next is None


def _schedule(*ends: tuple[str, str]) -> dict[str, Any]:
    return {"schedule": [{"wbsNodeId": node_id, "end": end} for node_id, end in ends]}


def test_a_milestone_is_judged_by_its_own_packages_not_the_project_finish() -> None:
    """QA pack VIEW-001: MS1 'Ingestion (W01-W03)' is due Apr 30, 2027; Plan v1 finishes
    W01-W03 by Feb 26 and the whole project on Jun 23. MS1 is on track."""
    ms1 = MilestoneFact("MS1 Ingestion (W01-W03)", date(2027, 4, 30), frozenset({"d1", "w1", "w3"}))
    plan_v1 = _schedule(("w1", "2027-01-29"), ("w3", "2027-02-26"), ("w9", "2027-06-23"))
    finishes = planned_finishes(plan_v1)
    summary = milestone_summary([ms1], date(2027, 6, 23), finishes, date(2026, 11, 14))
    assert summary.next is not None
    assert summary.next.status == "On track"
    # One of its own packages after its date puts it at risk, however early the rest is.
    late = planned_finishes(_schedule(("w1", "2027-01-29"), ("w3", "2027-05-03")))
    assert milestone_summary([ms1], date(2027, 5, 3), late, TODAY).next.status == "At risk"  # type: ignore[union-attr]
    # A plan that schedules none of its packages hasn't planned it.
    other = planned_finishes(_schedule(("w9", "2027-06-23"), ("bad", "not a date")))
    assert other == {"w9": date(2027, 6, 23)}
    assert milestone_summary([ms1], date(2027, 6, 23), other, TODAY).next.status == (  # type: ignore[union-attr]
        "Not planned yet"
    )
    applied = build_overview(
        OverviewFacts(
            project=_project(Store(), uuid.uuid4()),
            milestones=[ms1],
            applied_run=_run(plan_v1, completion=date(2027, 6, 23)),
        ),
        date(2026, 11, 14),
    )
    assert applied.milestones.next is not None
    assert applied.milestones.next.status == "On track"


async def test_milestones_cover_their_wbs_node_and_everything_under_it() -> None:
    root, deliverable, w1, w2, other = (uuid.uuid4() for _ in range(5))
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
                {"id": w1, "parent_id": deliverable},
                {"id": w2, "parent_id": deliverable},
                {"id": other, "parent_id": root},
            ]
        ),
    )
    found = await OverviewRepository(conn)._milestones(uuid.uuid4())  # type: ignore[arg-type]
    assert found == [
        MilestoneFact("MS1", date(2027, 4, 30), frozenset({str(deliverable), str(w1), str(w2)})),
        MilestoneFact("Go-live", date(2027, 7, 30)),
    ]


def test_next_step_follows_the_workflow() -> None:
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


def _project(store: Store, owner: uuid.UUID) -> Project:
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


def test_build_overview_without_a_plan_never_invents_numbers() -> None:
    store = Store()
    project = _project(store, store.add_user())
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
    assert [r["level"] for r in body["risks"]] == ["Medium", "Medium"]
    assert body["risks"][1]["source"] == "From AI intake, not reviewed yet"
    assert body["nextStep"] == {"label": "Run AI Project Intake on your notes", "step": "intake"}


def test_build_overview_with_an_applied_plan() -> None:
    store = Store()
    project = _project(store, store.add_user())
    overview = build_overview(
        OverviewFacts(
            project=project,
            policy=ANSWERED,
            progress=READY,
            milestones=[MilestoneFact("Phase 1", date(2026, 10, 1))],
            applied_run=_run(),
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
    assert [k["label"] for k in plan["kpis"]] == ["Go-live"]
    assert body["risks"] == [
        {
            "level": "High",
            "text": "Sara Kim is booked at 110% in Plan v2",
            "source": "From the plan",
        }
    ]
    assert body["milestones"]["next"]["status"] == "On track"
    assert [f["source"] for f in body["findings"]] == ["wbs_validate"] * 3 + ["optimization"]
    assert body["nextStep"]["step"] == "changes"


# --------------------------------------------------------------------------- endpoint
class FakeOverviews:
    def __init__(self, user_id: uuid.UUID) -> None:
        self.user_id = user_id

    async def facts(self, project: Project, user_id: uuid.UUID) -> OverviewFacts:
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
            applied_run=_run(),
            notifications=[note],
            notification_total=12,
        )


def test_overview_endpoint(
    app: FastAPI,
    client: TestClient,
    store: Store,
    auth: Callable[[uuid.UUID], dict[str, str]],
) -> None:
    def overviews(principal: CurrentPrincipal) -> FakeOverviews:
        return FakeOverviews(principal.user_id)

    app.dependency_overrides[deps.get_overview_repo] = overviews
    owner = store.add_user()
    project = _project(store, owner)

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
