"""Fake optimization run repository and helpers for the optimization endpoint tests.

Purpose: Let the tests set the loaded inputs, sheet facts, earlier owners, change requests and race
conditions, and inspect the runs a request created.
How it fits: The fixtures in tests/optimization_api/conftest.py install `FakeRuns` in place of
`OptimizationRepository`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

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
    """What the fake run tables hold and the database states a test can switch on."""

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
    """Stand-in for `OptimizationRepository` over the run store."""

    def __init__(self, store: Store, runs: RunStore, user_id: uuid.UUID) -> None:
        """Act as the given user over the store and the run store."""
        super().__init__(store, user_id)
        self.run_store = runs

    async def project(self, project_id: uuid.UUID) -> ProjectContext | None:
        """Return the visible project's context, or None."""
        project = self.store.projects.get(project_id)
        if project is None or self._role(project_id) is None:
            return None
        return ProjectContext(
            project.id,
            project.org_id,
            project.name,
            project.start_date,
            project.target_end_date,
            None,
        )

    async def lock_run_creation(self, project_id: uuid.UUID) -> bool:
        """Refuse the lock while the test says another request is starting a run."""
        return project_id not in self.run_store.starting

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        """Return the project's queued or running run, flagged stale after `stale_after_s`."""
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_s)
        for run in self.run_store.runs.values():
            if run["project_id"] == project_id and run["status"] in ("queued", "running"):
                return ActiveRun(run["id"], run["created_at"] < cutoff)
        return None

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        """Mark a run failed with the error."""
        self.run_store.runs[run_id].update(status="failed", error=error)

    async def load_input(self, project: ProjectContext, today: date) -> PlanningInput:
        """Return the input the test made ready, or an empty one."""
        return self.run_store.inputs.get(
            project.id, PlanningInput(str(project.id), project.name, today, (), ())
        )

    async def input_facts(
        self, project: ProjectContext, planning_input: PlanningInput
    ) -> InputFacts:
        """Return the sheet facts of the input plus what the test set for the project."""
        return facts_from_input(planning_input, **self.run_store.facts.get(project.id, {}))

    async def packages_without_duration(self, project_id: uuid.UUID) -> list[str]:
        """Return the work packages the test marked as having no duration."""
        return self.run_store.without_duration.get(project_id, [])

    async def baseline_owners(
        self, project_id: uuid.UUID, run_id: uuid.UUID
    ) -> dict[str, str] | None:
        """Return an earlier run's planned owners, or None for an unknown run."""
        run = self.run_store.runs.get(run_id)
        if run is None or run["project_id"] != project_id:
            return None
        return self.run_store.owners.get(run_id, {})

    async def applied_run(self, project_id: uuid.UUID) -> AppliedRun | None:
        """Return the plan in force (the latest applied version) and its inputs hash."""
        applied = [
            run
            for run in self.run_store.runs.values()
            if run["project_id"] == project_id and run.get("applied_at") is not None
        ]
        if not applied:
            return None
        run = max(applied, key=lambda entry: entry["applied_at"])
        return AppliedRun(run["id"], (run.get("options") or {}).get("inputsHash"))

    async def change_request_status(
        self, project_id: uuid.UUID, change_request_id: uuid.UUID
    ) -> str | None:
        """Return a change request's status when it belongs to the project."""
        change = self.run_store.changes.get(change_request_id)
        return change["status"] if change and change["project_id"] == project_id else None

    async def mark_replanning(
        self, project_id: uuid.UUID, change_request_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        """Point the change request at the re-plan run."""
        self.run_store.changes[change_request_id].update(
            status="replanning", proposed_run_id=run_id
        )

    async def create(
        self,
        project_id: uuid.UUID,
        snapshot: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """Store a queued run; RLS error for viewers, `ActiveRunExistsError` on a simulated race."""
        if not await self.can_write(project_id):
            raise rls_violation()
        if self.run_store.race:
            raise ActiveRunExistsError
        rid = uuid.uuid4()
        self.run_store.runs[rid] = {
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
        """Return the run as the API model when the acting user can see its project."""
        if self._role(run["project_id"]) is None:
            return None
        return OptimizationRun.model_validate(
            {column: value for column, value in run.items() if column != "snapshot"}
        )

    async def get(self, project_id: uuid.UUID, run_id: uuid.UUID) -> OptimizationRun | None:
        """Return a visible run of the project, or None."""
        run = self.run_store.runs.get(run_id)
        return self._visible(run) if run and run["project_id"] == project_id else None

    async def latest(self, project_id: uuid.UUID) -> OptimizationRun | None:
        """Return the project's newest visible run, or None."""
        rows = [run for run in self.run_store.runs.values() if run["project_id"] == project_id]
        if not rows:
            return None
        return self._visible(max(rows, key=lambda run: run["created_at"]))


def mark_ready(run_store: RunStore, project_id: str) -> None:
    """Give the project a ready planning input: one work package and one person."""
    pid = uuid.UUID(project_id)
    run_store.inputs[pid] = PlanningInput(
        project_id=project_id,
        project_name="CRM",
        start_date=date(2026, 10, 5),
        tasks=(Task("t1", "1.1.1", "Design", 5),),
        employees=(Employee("e1", "Mark Chen"),),
    )


def runs_url(project_id: str, suffix: str = "") -> str:
    """Return the project's optimization runs URL, with an optional suffix."""
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
