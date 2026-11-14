"""Changes & Versions (step 7): reading notes, questions, overrides, and the endpoints (fakes)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.llm import LLM
from app.api.v1 import changes as changes_api
from app.changes.compare import build_comparison
from app.changes.context import ChangeContext, NodeRow, PersonRef, PlanRow, RunMeta, TaskRef
from app.changes.derive import (
    InvalidEditError,
    build_impact,
    derive_overrides,
    from_edits,
    validate_answers,
)
from app.changes.detect import detect
from app.changes.llm import ChangeDetector, ProposedChange, ProposedChanges, to_changes
from app.core.security import CurrentPrincipal
from app.domain.changes import (
    ChangeEdits,
    ChangeImpact,
    ChangeRequest,
    ChangeSource,
    ChangeStatus,
    PlanOverrides,
)
from app.domain.common import OrgRole, SolverStatus
from app.repositories.access import ProjectRef
from app.repositories.changes import InForce, RunState, to_change
from tests.conftest import Store

Auth = Callable[[uuid.UUID], dict[str, str]]

NOTES = """Steering Committee — March 3, 2025
Attendees: Robert Walsh (VP Eng), Sara Kim (Lead Architect), Mark Chen (Data Engineer)

• Robert: Workday has to be added as a data source. It must be in the platform at go-live.
• Robert: Finance is trimming the budget to $365K.
• Mark: The Salesforce connector is done.
• SAP vendor update: sandbox access slips two weeks.
• Sara: Internal database pipelines are on track."""

MARK, SARA = uuid.uuid4(), uuid.uuid4()
SF, SAP, ETL = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _ctx() -> ChangeContext:
    return ChangeContext(
        budget_cents=37_500_000,
        deadline=date(2025, 9, 30),
        start=date(2025, 1, 13),
        tasks=(
            TaskRef(SF, "1.1.1", "Salesforce connector development", 15, MARK),
            TaskRef(SAP, "1.1.2", "SAP integration setup", 20, None),
            TaskRef(ETL, "1.2.2", "ETL pipeline development", 25, MARK),
        ),
        people=(PersonRef(MARK, "Mark Chen", 30), PersonRef(SARA, "Sara Kim", 32)),
        edges=((SAP, ETL),),
        plan_version=1,
        plan_cost_cents=34_860_000,
        plan_finish=date(2025, 9, 19),
        today=date(2025, 3, 3),
    )


# --------------------------------------------------------------------------- rules
def test_rules_read_the_steering_committee_notes() -> None:
    found = detect(NOTES, _ctx())
    kinds = [(c.kind, c.target, c.to) for c in found]
    assert kinds == [
        ("scope", None, "Workday has to be added as a data source."),
        ("budget", None, 36_500_000),
        ("duration", SF, 0),
        ("duration", None, None),  # "SAP" only partly names 1.1.2: asked, not guessed
    ]
    budget = found[1]
    assert (budget.from_, budget.quote, budget.speaker) == (
        37_500_000,
        "Finance is trimming the budget to $365K.",
        "Robert",
    )
    sap = found[3]
    assert [(o.id, o.from_, o.to) for o in sap.candidates] == [(SAP, 20, 30)]
    assert all(c.quote and c.quote in NOTES for c in found)


def test_rules_never_invent() -> None:
    ctx = _ctx()
    # A meeting date isn't a deadline, "on track" isn't a change, a vague slip names nothing.
    assert detect("Kick-off — March 3, 2025\nEverything is on track.", ctx) == []
    assert detect("Things might slip a bit.", ctx) == []
    found = detect("Mark Chen is leaving the company. Go-live moves to Oct 17, 2025.", ctx)
    assert [(c.kind, c.target, c.to) for c in found] == [
        ("capacity", MARK, 0),
        ("deadline", None, "2025-10-17"),
    ]


def test_questions_and_overrides_from_notes() -> None:
    ctx = _ctx()
    impact = build_impact(detect(NOTES, ctx), "notes", ctx, "rules")
    by_id = {q.id: q for q in impact.questions}
    assert set(by_id) == {"budget", "target:c4"}
    assert [o.value for o in by_id["budget"].options] == ["36500000", "keep"]
    assert "Plan v1 uses $375,000" in by_id["budget"].text
    assert {t.code for t in impact.affects.tasks} == {"1.1.1", "1.1.2", "1.2.2"}
    assert [p.name for p in impact.affects.people] == ["Mark Chen"]

    assert derive_overrides(impact, {"budget": "36500000"}) is None  # one still open
    o = derive_overrides(impact, {"budget": "36500000", "target:c4": str(SAP)})
    assert o == PlanOverrides(
        budget_total_cents=36_500_000, duration_days={str(SF): 0, str(SAP): 30}
    )
    assert o.model_dump(mode="json") == {
        "budgetTotalCents": 36_500_000,
        "durationDays": {str(SF): 0, str(SAP): 30},
    }
    kept = derive_overrides(impact, {"budget": "keep", "target:c4": "none"})
    assert kept == PlanOverrides(duration_days={str(SF): 0})
    assert validate_answers(impact.questions, {"budget": "1"}) is not None
    assert validate_answers(impact.questions, {"nope": "keep"}) is not None


def test_edits_and_progress_are_taken_as_they_are() -> None:
    ctx = _ctx()
    edits = ChangeEdits(
        budget_total_cents=36_500_000,
        must_finish_by=date(2025, 9, 30),  # unchanged: not a change
        hours_per_week={MARK: 20},
        progress={SAP: 50, SF: 100, ETL: 0},
    )
    found = from_edits(edits, ctx)
    assert [(c.kind, c.target, c.to) for c in found] == [
        ("budget", None, 36_500_000),
        ("capacity", MARK, 20),
        ("duration", SAP, 10),
        ("duration", SF, 0),
    ]
    impact = build_impact(found, "edits", ctx, "rules")
    assert impact.questions == []
    assert derive_overrides(impact, {}) == PlanOverrides(
        budget_total_cents=36_500_000,
        duration_days={str(SAP): 10, str(SF): 0},
        hours_per_week={str(MARK): 20},
    )
    with pytest.raises(InvalidEditError):
        from_edits(ChangeEdits(duration_days={uuid.uuid4(): 5}), ctx)


# --------------------------------------------------------------------------- Claude path
def test_claude_changes_are_checked_against_notes_and_project() -> None:
    ctx = _ctx()
    out = ProposedChanges(
        changes=[
            ProposedChange(
                kind="budget",
                value="$365,000",
                quote="Finance is trimming the budget to $365K.",
                confidence=0.9,
            ),
            ProposedChange(
                kind="duration",
                target="1.1.2",
                value="30",
                quote="sandbox access slips two weeks.",
                confidence=0.7,
            ),
            # Not in the notes / unknown person: dropped.
            ProposedChange(kind="budget", value="1", quote="Budget is $1.", confidence=1),
            ProposedChange(
                kind="capacity",
                target="Nobody",
                value="0",
                quote="The Salesforce connector is done.",
                confidence=1,
            ),
        ]
    )
    kept = to_changes(out, NOTES, ctx)
    assert [(c.kind, c.target, c.to) for c in kept] == [
        ("budget", None, 36_500_000),
        ("duration", SAP, 30),
    ]


class _BrokenLLM(LLM):
    def __init__(self) -> None:
        super().__init__(client=None, model="claude-sonnet-5-5")

    @property
    def configured(self) -> bool:
        return True

    def parse(self, **_: Any) -> Any:
        raise RuntimeError("boom")


async def test_detector_falls_back_to_rules() -> None:
    ctx = _ctx()
    found, model = await ChangeDetector(_BrokenLLM()).detect(NOTES, ctx)
    assert model == "rules"
    assert [c.kind for c in found] == ["scope", "budget", "duration", "duration"]
    _, model = await ChangeDetector(None).detect(NOTES, ctx)
    assert model == "rules"


# --------------------------------------------------------------------------- compare
def test_comparison_shows_what_moved_and_why() -> None:
    ctx = _ctx()
    impact = build_impact(detect(NOTES, ctx), "notes", ctx, "rules")
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
        NodeRow(SF, group, 3, "1.1.1", "Salesforce connector development"),
        NodeRow(SAP, group, 3, "1.1.2", "SAP integration setup"),
        NodeRow(ETL, group, 3, "1.2.2", "ETL pipeline development"),
    ]
    before = [
        PlanRow(SF, MARK, None, 0, 2),
        PlanRow(SAP, None, "SAP specialist", 3, 6),
        PlanRow(ETL, MARK, None, 7, 11),
    ]
    after = [PlanRow(SAP, None, "SAP specialist", 3, 8), PlanRow(ETL, MARK, None, 9, 13)]
    cmp = build_comparison(base, before, proposed, after, nodes, {MARK: "Mark Chen"}, impact)
    rows = {r.code: r for r in cmp.rows}
    assert rows["1.1.1"].after is None
    assert rows["1.1.1"].why == "Done, nothing left to plan"
    assert (rows["1.1.2"].shift_days, rows["1.1.2"].why) == (14, "Effort 4w → 6w")
    assert (rows["1.2.2"].shift_days, rows["1.2.2"].why) == (14, "Follows 1.1.2")
    assert rows["1.2.2"].group_code == "1.1"
    assert rows["1.1.2"].before is not None
    assert rows["1.1.2"].before.start == date(2025, 2, 3)
    assert cmp.base.budget_cents == 37_500_000


# --------------------------------------------------------------------------- API (fakes)
@dataclass
class ChangeStore:
    rows: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    runs: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    applied: list[uuid.UUID] = field(default_factory=list)
    written: list[PlanOverrides] = field(default_factory=list)


class FakeChanges:
    def __init__(self, store: Store, cs: ChangeStore, user_id: uuid.UUID) -> None:
        self.s, self.cs, self.uid = store, cs, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        p = self.s.projects.get(project_id)
        if p is None or self._role(project_id) is None:
            return None
        return ProjectRef(p.id, p.org_id, p.name, p.start_date, p.target_end_date, p.budget_cents)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def in_force(self, project_id: uuid.UUID) -> InForce | None:
        applied = [
            (rid, r)
            for rid, r in self.cs.runs.items()
            if r["project_id"] == project_id and r["applied"]
        ]
        if not applied:
            return None
        rid, r = max(applied, key=lambda x: x[1]["version"] or 0)
        return InForce(rid, r["version"], 34_860_000, date(2025, 9, 19))

    async def context(
        self, project: ProjectRef, in_force: InForce | None, today: date
    ) -> ChangeContext:
        ctx = _ctx()
        return ChangeContext(
            budget_cents=ctx.budget_cents,
            deadline=ctx.deadline,
            start=ctx.start,
            tasks=ctx.tasks,
            people=ctx.people,
            edges=ctx.edges,
            plan_version=in_force.version if in_force else None,
            today=today,
        )

    async def create(
        self,
        project_id: uuid.UUID,
        source: ChangeSource,
        input_text: str | None,
        impact: ChangeImpact,
        base_run_id: uuid.UUID,
    ) -> uuid.UUID:
        cid = uuid.uuid4()
        self.cs.rows[cid] = {
            "id": cid,
            "project_id": project_id,
            "source": source,
            "input_text": input_text,
            "status": "ready",
            "impact": impact.model_dump(mode="json"),
            "answers": {},
            "base_run_id": base_run_id,
            "proposed_run_id": None,
            "error": None,
            "created_at": datetime.now(UTC),
            "decided_at": None,
        }
        return cid

    def _view(self, row: dict[str, Any]) -> ChangeRequest:
        base = self.cs.runs.get(row["base_run_id"]) or {}
        prop = self.cs.runs.get(row["proposed_run_id"]) or {}
        return to_change(
            {
                **row,
                "base_version": base.get("version"),
                "proposed_run_status": prop.get("status"),
            }
        )

    async def get(self, change_id: uuid.UUID) -> ChangeRequest | None:
        row = self.cs.rows.get(change_id)
        if row is None or self._role(row["project_id"]) is None:
            return None
        return self._view(row)

    async def list(self, project_id: uuid.UUID) -> list[ChangeRequest]:
        rows = [r for r in self.cs.rows.values() if r["project_id"] == project_id]
        return [self._view(r) for r in sorted(rows, key=lambda r: r["created_at"], reverse=True)]

    async def save_answers(
        self, change_id: uuid.UUID, answers: dict[str, str], *, reset_proposal: bool
    ) -> None:
        row = self.cs.rows[change_id]
        row["answers"] = answers
        if reset_proposal:
            row.update(status="ready", proposed_run_id=None)

    async def decide(
        self, change_id: uuid.UUID, status: ChangeStatus, proposed_run_id: uuid.UUID | None
    ) -> None:
        row = self.cs.rows[change_id]
        row.update(status=status.value, decided_at=datetime.now(UTC))
        if proposed_run_id:
            row["proposed_run_id"] = proposed_run_id

    async def run(self, run_id: uuid.UUID) -> RunState | None:
        r = self.cs.runs.get(run_id)
        if r is None:
            return None
        return RunState(run_id, r["project_id"], r["status"], r["applied"], r["version"])

    async def apply_run(self, run_id: uuid.UUID) -> None:
        r = self.cs.runs[run_id]
        r.update(
            applied=True,
            version=1 + max((x["version"] or 0) for x in self.cs.runs.values()),
        )
        self.cs.applied.append(run_id)

    async def write_overrides(self, project: ProjectRef, o: PlanOverrides) -> None:
        self.cs.written.append(o)


@pytest.fixture
def change_store() -> ChangeStore:
    return ChangeStore()


@pytest.fixture
def change_client(app: FastAPI, store: Store, change_store: ChangeStore) -> Iterator[TestClient]:
    def repo(principal: CurrentPrincipal) -> FakeChanges:
        return FakeChanges(store, change_store, principal.user_id)

    app.dependency_overrides[changes_api.get_change_repo] = repo
    app.dependency_overrides[changes_api.get_change_detector] = lambda: ChangeDetector(None)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID, str]:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    r = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "EDP"}, headers=auth(user))
    return user, org, r.json()["id"]


def _run(cs: ChangeStore, pid: str, *, applied: bool, version: int | None = None) -> uuid.UUID:
    rid = uuid.uuid4()
    cs.runs[rid] = {
        "project_id": uuid.UUID(pid),
        "status": SolverStatus.OPTIMAL,
        "applied": applied,
        "version": version,
    }
    return rid


def test_change_flow_notes_to_approved_plan(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    user, _, pid = _project(change_client, store, auth)
    h = auth(user)
    url = f"/api/v1/projects/{pid}/changes"

    r = change_client.post(url, json={"source": "notes", "text": NOTES}, headers=h)
    assert r.status_code == 409  # nothing applied yet
    v1 = _run(change_store, pid, applied=True, version=1)

    assert change_client.post(url, json={"source": "notes"}, headers=h).status_code == 422
    r = change_client.post(url, json={"source": "notes", "text": NOTES}, headers=h)
    assert r.status_code == 202, r.text
    change = r.json()
    cid = change["id"]
    assert (change["status"], change["baseRunId"], change["baseVersion"]) == ("ready", str(v1), 1)
    assert change["impact"]["model"] == "rules"
    assert change["overrides"] is None
    assert {q["id"] for q in change["impact"]["questions"]} == {"budget", "target:c4"}
    assert [c["id"] for c in change_client.get(url, headers=h).json()] == [cid]

    # Approving before a re-plan exists is refused.
    assert change_client.post(f"/api/v1/changes/{cid}/approve", headers=h).status_code == 409
    bad = {"answers": {"budget": "123"}}
    assert change_client.patch(f"/api/v1/changes/{cid}", json=bad, headers=h).status_code == 422
    r = change_client.patch(
        f"/api/v1/changes/{cid}",
        json={"answers": {"budget": "36500000", "target:c4": str(SAP)}},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["overrides"] == {
        "budgetTotalCents": 36_500_000,
        "durationDays": {str(SF): 0, str(SAP): 30},
    }

    # C1's re-plan run links itself to the change; it is proposed once it succeeds.
    v2 = _run(change_store, pid, applied=False)
    change_store.runs[v2]["status"] = SolverStatus.RUNNING
    change_store.rows[uuid.UUID(cid)].update(status="replanning", proposed_run_id=v2)
    assert change_client.post(f"/api/v1/changes/{cid}/approve", headers=h).status_code == 409
    change_store.runs[v2]["status"] = SolverStatus.OPTIMAL
    assert change_client.get(f"/api/v1/changes/{cid}", headers=h).json()["status"] == "proposed"

    r = change_client.post(f"/api/v1/changes/{cid}/approve", json={}, headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["decidedAt"] is not None) == ("approved", True)
    assert change_store.applied == [v2]
    assert change_store.runs[v2]["version"] == 2
    assert change_store.written == [
        PlanOverrides(budget_total_cents=36_500_000, duration_days={str(SF): 0, str(SAP): 30})
    ]
    assert change_client.post(f"/api/v1/changes/{cid}/reject", headers=h).status_code == 409


def test_edits_reject_and_permissions(
    change_client: TestClient, store: Store, change_store: ChangeStore, auth: Auth
) -> None:
    user, org, pid = _project(change_client, store, auth)
    h = auth(user)
    _run(change_store, pid, applied=True, version=1)
    url = f"/api/v1/projects/{pid}/changes"

    same = {"source": "edits", "edits": {"budgetTotalCents": 37_500_000}}
    assert change_client.post(url, json=same, headers=h).status_code == 422
    unknown = {"source": "progress", "edits": {"progress": {str(uuid.uuid4()): 50}}}
    assert change_client.post(url, json=unknown, headers=h).status_code == 422

    r = change_client.post(
        url, json={"source": "progress", "edits": {"progress": {str(SAP): 50}}}, headers=h
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["impact"]["questions"] == []
    assert body["overrides"] == {"durationDays": {str(SAP): 10}}

    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    vh = auth(viewer)
    assert change_client.get(url, headers=vh).status_code == 200
    assert change_client.post(f"/api/v1/changes/{body['id']}/reject", headers=vh).status_code == 403
    outsider = store.add_user("Olly Outsider")
    assert (
        change_client.get(f"/api/v1/changes/{body['id']}", headers=auth(outsider)).status_code
        == 404
    )

    r = change_client.post(f"/api/v1/changes/{body['id']}/reject", headers=h)
    assert (r.status_code, r.json()["status"]) == (200, "rejected")
