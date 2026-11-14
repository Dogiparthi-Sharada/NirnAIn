"""Shared step 7 test data and fakes.

What: the approved mockup's Steering Committee notes, a project context with three work
packages and two people, and an in-memory change repository for the API tests.
Why: the change tests (rules, API, team changes) run without a database; one fake keeps them
consistent. How it fits: used by test_changes_rules, test_changes_api and test_changes_team.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import changes as changes_api
from app.changes.context import ChangeContext, PersonRef, TaskRef
from app.changes.llm import ChangeDetector
from app.changes.team import is_addition
from app.core.security import CurrentPrincipal
from app.domain.changes import (
    ChangeImpact,
    ChangeRequest,
    ChangeSource,
    ChangeStatus,
    DetectedChange,
    PlanOverrides,
)
from app.domain.common import OrgRole, SolverStatus
from app.repositories.access import ProjectRef
from app.repositories.change_plans import InForce, RunState
from app.repositories.changes import to_change
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
SALESFORCE, SAP, ETL = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

# QA's mid-project meeting (Plan v2 in force): a specialist, Leo's hours, deadline, budget.
QA_NOTES = (
    "Steering committee, Jan 15. Jane Doe: We agreed to bring in an outside security "
    "specialist for the compliance review. Raj Patel: Leo can only give 16 hours a week from "
    "February. Jane Doe: The go-live can move to July 31, 2027 if needed, and the budget goes "
    "up to $500,000."
)
LEO, ANA, SAM = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
REVIEW, BUILD = uuid.uuid4(), uuid.uuid4()


def project_context() -> ChangeContext:
    """Plan v1 in force: $375,000 budget, Sep 30, 2025 deadline, SAP → ETL dependency."""
    return ChangeContext(
        budget_cents=37_500_000,
        deadline=date(2025, 9, 30),
        start=date(2025, 1, 13),
        tasks=(
            TaskRef(SALESFORCE, "1.1.1", "Salesforce connector development", 15, MARK),
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


def qa_context() -> ChangeContext:
    """QA's project: Leo, Ana and Sam; Sam owns the compliance review; Plan v2 in force."""
    return ChangeContext(
        budget_cents=45_000_000,
        deadline=date(2027, 6, 30),
        start=date(2026, 11, 30),
        tasks=(
            TaskRef(REVIEW, "1.1.1", "Compliance review", 10, SAM),
            TaskRef(BUILD, "1.1.2", "Pipeline build", 20, LEO),
        ),
        people=(
            PersonRef(LEO, "Leo Martins", 40),
            PersonRef(ANA, "Ana Souza", 24),
            PersonRef(SAM, "Sam Okafor", 16),
        ),
        plan_version=2,
        plan_cost_cents=30_000_000,
        plan_finish=date(2027, 1, 22),
        today=date(2027, 1, 15),
        skills=("Security & Compliance Review", "Data Engineering"),
    )


@dataclass
class ChangeStore:
    """What the fake repository holds and records (rows, runs, writes on approval)."""

    rows: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    runs: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    applied: list[uuid.UUID] = field(default_factory=list)
    written: list[PlanOverrides] = field(default_factory=list)
    # Organization employees by id: {"name", "active"}; reserved candidates are inactive.
    employees: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    requirements: list[str] = field(default_factory=list)
    # The project inputs changes are read against (project_context() unless set).
    context: ChangeContext | None = None
    # Hash of the saved planning inputs; runs are solved from it unless a test says otherwise.
    inputs_hash: str = "saved-inputs"


class FakeChanges:
    def __init__(self, store: Store, change_store: ChangeStore, user_id: uuid.UUID) -> None:
        self.store, self.change_store, self.user_id = store, change_store, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        project = self.store.projects.get(project_id)
        return self.store.role(project.org_id, self.user_id) if project else None

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        project = self.store.projects.get(project_id)
        if project is None or self._role(project_id) is None:
            return None
        return ProjectRef(
            project.id,
            project.org_id,
            project.name,
            project.start_date,
            project.target_end_date,
            project.budget_cents,
        )

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def in_force(self, project_id: uuid.UUID) -> InForce | None:
        applied = [
            (run_id, run)
            for run_id, run in self.change_store.runs.items()
            if run["project_id"] == project_id and run["applied"]
        ]
        if not applied:
            return None
        run_id, run = max(applied, key=lambda entry: entry[1]["version"] or 0)
        return InForce(run_id, run["version"], 34_860_000, date(2025, 9, 19))

    async def context(
        self, project: ProjectRef, in_force: InForce | None, today: date
    ) -> ChangeContext:
        context = self.change_store.context or project_context()
        return ChangeContext(
            budget_cents=context.budget_cents,
            deadline=context.deadline,
            start=context.start,
            tasks=context.tasks,
            people=context.people,
            edges=context.edges,
            plan_version=in_force.version if in_force else None,
            today=today,
            skills=context.skills,
            contractor_rate_cents=context.contractor_rate_cents,
        )

    async def create(
        self,
        project_id: uuid.UUID,
        source: ChangeSource,
        input_text: str | None,
        impact: ChangeImpact,
        base_run_id: uuid.UUID,
    ) -> uuid.UUID:
        change_id = uuid.uuid4()
        self.change_store.rows[change_id] = {
            "id": change_id,
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
        return change_id

    def _view(self, row: dict[str, Any]) -> ChangeRequest:
        base = self.change_store.runs.get(row["base_run_id"]) or {}
        proposed_run = self.change_store.runs.get(row["proposed_run_id"]) or {}
        return to_change(
            {
                **row,
                "base_version": base.get("version"),
                "proposed_run_status": proposed_run.get("status"),
            }
        )

    async def get(self, change_id: uuid.UUID) -> ChangeRequest | None:
        row = self.change_store.rows.get(change_id)
        if row is None or self._role(row["project_id"]) is None:
            return None
        return self._view(row)

    async def list(self, project_id: uuid.UUID) -> list[ChangeRequest]:
        rows = [row for row in self.change_store.rows.values() if row["project_id"] == project_id]
        return [
            self._view(row) for row in sorted(rows, key=lambda row: row["created_at"], reverse=True)
        ]

    async def save_answers(
        self, change_id: uuid.UUID, answers: dict[str, str], *, reset_proposal: bool
    ) -> None:
        row = self.change_store.rows[change_id]
        row["answers"] = answers
        if reset_proposal:
            row.update(status="ready", proposed_run_id=None)

    async def decide(
        self, change_id: uuid.UUID, status: ChangeStatus, proposed_run_id: uuid.UUID | None
    ) -> None:
        row = self.change_store.rows[change_id]
        row.update(status=status.value, decided_at=datetime.now(UTC))
        if proposed_run_id:
            row["proposed_run_id"] = proposed_run_id

    async def run(self, run_id: uuid.UUID) -> RunState | None:
        run = self.change_store.runs.get(run_id)
        if run is None:
            return None
        return RunState(
            run_id,
            run["project_id"],
            run["status"],
            run["applied"],
            run["version"],
            inputs_hash=run.get("inputs_hash"),
            baseline_run_id=run.get("baseline_run_id"),
        )

    async def saved_inputs_hash(self, project: ProjectRef, today: date) -> str:
        return self.change_store.inputs_hash

    async def apply_run(self, run_id: uuid.UUID) -> None:
        run = self.change_store.runs[run_id]
        run.update(
            applied=True,
            version=1 + max((entry["version"] or 0) for entry in self.change_store.runs.values()),
        )
        self.change_store.applied.append(run_id)

    async def write_overrides(self, project: ProjectRef, overrides: PlanOverrides) -> None:
        self.change_store.written.append(overrides)
        for member in overrides.add_members or []:
            self.change_store.employees[member.employee_id]["active"] = True

    async def reserve_members(
        self, project: ProjectRef, found: list[DetectedChange]
    ) -> list[DetectedChange]:
        reserved = []
        employees = self.change_store.employees
        for change in found:
            if is_addition(change) and change.member and change.target is None:
                name = change.member.full_name.casefold()
                employee_id = next(
                    (
                        known
                        for known, row in employees.items()
                        if row["active"] and row["name"].casefold() == name
                    ),
                    None,
                )
                if employee_id is None:
                    employee_id = uuid.uuid4()
                    employees[employee_id] = {"name": change.member.full_name, "active": False}
                reserved.append(change.model_copy(update={"target": employee_id}))
            else:
                reserved.append(change)
        return reserved

    async def release_members(self, project: ProjectRef, employee_ids: list[uuid.UUID]) -> None:
        for employee_id in employee_ids:
            if not self.change_store.employees[employee_id]["active"]:
                del self.change_store.employees[employee_id]

    async def add_requirements(self, project_id: uuid.UUID, texts: list[str]) -> None:
        self.change_store.requirements.extend(texts)


def use_fake_changes(app: FastAPI, store: Store, change_store: ChangeStore) -> None:
    """Route step 7 to the in-memory repository and the rules reader (no Claude)."""

    def repo(principal: CurrentPrincipal) -> FakeChanges:
        return FakeChanges(store, change_store, principal.user_id)

    app.dependency_overrides[changes_api.get_change_repo] = repo
    app.dependency_overrides[changes_api.get_change_detector] = lambda: ChangeDetector(None)


def new_project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID, str]:
    """(owner, org, project id) of a fresh project owned by Jane Doe."""
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    response = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "EDP"}, headers=auth(user))
    return user, org, response.json()["id"]


def add_run(
    change_store: ChangeStore, project_id: str, *, applied: bool, version: int | None = None
) -> uuid.UUID:
    """A solved run of the project (applied = a plan version in force)."""
    run_id = uuid.uuid4()
    change_store.runs[run_id] = {
        "project_id": uuid.UUID(project_id),
        "status": SolverStatus.OPTIMAL,
        "applied": applied,
        "version": version,
        "inputs_hash": change_store.inputs_hash,
    }
    return run_id
