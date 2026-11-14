"""Moved test support."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.domain.optimization import OptimizationRun
from app.optimization.inputs import InputFacts, facts_from_input
from app.optimization.model import Employee, PlanningInput, Task
from app.repositories.optimization import (
    ActiveRun,
    ActiveRunExistsError,
    AppliedRun,
    ProjectContext,
)
from tests.support.store import ProjectScopedFake, Store, rls_violation


@dataclass
class RunStore:
    runs: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    inputs: dict[uuid.UUID, PlanningInput] = field(default_factory=dict)
    # Sheet facts only the database knows (answers, estimate confirmations), per project.
    facts: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    # Planned owners (WBS node id -> employee id) of earlier runs.
    owners: dict[uuid.UUID, dict[str, str]] = field(default_factory=dict)
    # Change requests: id -> {"project_id", "status", "proposed_run_id"}.
    changes: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    race: bool = False  # simulate a concurrent insert hitting the unique index
    starting: set[uuid.UUID] = field(default_factory=set)  # projects another request is starting
    # Work packages without a usable duration ('code name'), per project.
    without_duration: dict[uuid.UUID, list[str]] = field(default_factory=dict)


class FakeRuns(ProjectScopedFake):
    def __init__(self, store: Store, runs: RunStore, user_id: uuid.UUID) -> None:
        super().__init__(store, user_id)
        self.runs = runs

    async def project(self, project_id: uuid.UUID) -> ProjectContext | None:
        p = self.store.projects.get(project_id)
        if p is None or self._role(project_id) is None:
            return None
        return ProjectContext(p.id, p.org_id, p.name, p.start_date, p.target_end_date, None)

    async def lock_run_creation(self, project_id: uuid.UUID) -> bool:
        return project_id not in self.runs.starting

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_s)
        for run in self.runs.runs.values():
            if run["project_id"] == project_id and run["status"] in ("queued", "running"):
                return ActiveRun(run["id"], run["created_at"] < cutoff)
        return None

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        self.runs.runs[run_id].update(status="failed", error=error)

    async def load_input(self, project: ProjectContext, today: date) -> PlanningInput:
        return self.runs.inputs.get(
            project.id, PlanningInput(str(project.id), project.name, today, (), ())
        )

    async def input_facts(self, project: ProjectContext, inp: PlanningInput) -> InputFacts:
        return facts_from_input(inp, **self.runs.facts.get(project.id, {}))

    async def packages_without_duration(self, project_id: uuid.UUID) -> list[str]:
        return self.runs.without_duration.get(project_id, [])

    async def baseline_owners(
        self, project_id: uuid.UUID, run_id: uuid.UUID
    ) -> dict[str, str] | None:
        run = self.runs.runs.get(run_id)
        if run is None or run["project_id"] != project_id:
            return None
        return self.runs.owners.get(run_id, {})

    async def applied_run(self, project_id: uuid.UUID) -> AppliedRun | None:
        applied = [
            run
            for run in self.runs.runs.values()
            if run["project_id"] == project_id and run.get("applied_at") is not None
        ]
        if not applied:
            return None
        run = max(applied, key=lambda entry: entry["applied_at"])
        return AppliedRun(run["id"], (run.get("options") or {}).get("inputsHash"))

    async def change_request_status(
        self, project_id: uuid.UUID, change_request_id: uuid.UUID
    ) -> str | None:
        change = self.runs.changes.get(change_request_id)
        return change["status"] if change and change["project_id"] == project_id else None

    async def mark_replanning(
        self, project_id: uuid.UUID, change_request_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        self.runs.changes[change_request_id].update(status="replanning", proposed_run_id=run_id)

    async def create(
        self,
        project_id: uuid.UUID,
        snapshot: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        if not await self.can_write(project_id):
            raise rls_violation()
        if self.runs.race:
            raise ActiveRunExistsError
        rid = uuid.uuid4()
        self.runs.runs[rid] = {
            "id": rid,
            "project_id": project_id,
            "status": "queued",
            "solver": None,
            "solve_seconds": None,
            "error": None,
            "iis": None,
            "result": None,
            "created_at": datetime.now(UTC),
            "started_at": None,
            "finished_at": None,
            "options": options or {},
            "snapshot": snapshot,
        }
        return rid

    def _visible(self, run: dict[str, Any]) -> OptimizationRun | None:
        if self._role(run["project_id"]) is None:
            return None
        return OptimizationRun.model_validate({k: v for k, v in run.items() if k != "snapshot"})

    async def get(self, project_id: uuid.UUID, run_id: uuid.UUID) -> OptimizationRun | None:
        run = self.runs.runs.get(run_id)
        return self._visible(run) if run and run["project_id"] == project_id else None

    async def latest(self, project_id: uuid.UUID) -> OptimizationRun | None:
        rows = [r for r in self.runs.runs.values() if r["project_id"] == project_id]
        if not rows:
            return None
        return self._visible(max(rows, key=lambda r: r["created_at"]))


class FakeRunner:
    def __init__(self) -> None:
        self.submitted: list[uuid.UUID] = []

    async def submit(self, run_id: uuid.UUID) -> None:
        self.submitted.append(run_id)


def mark_ready(run_store: RunStore, project_id: str) -> None:
    pid = uuid.UUID(project_id)
    run_store.inputs[pid] = PlanningInput(
        project_id=project_id,
        project_name="CRM",
        start_date=date(2026, 10, 5),
        tasks=(Task("t1", "1.1.1", "Design", 5),),
        employees=(Employee("e1", "Mark Chen"),),
    )


def runs_url(project_id: str, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/optimization/runs{suffix}"


RUN_KEYS = {
    "id",
    "projectId",
    "status",
    "solver",
    "solveSeconds",
    "error",
    "iis",
    "result",
    "createdAt",
    "startedAt",
    "finishedAt",
    "version",
    "appliedAt",
    "options",
}
