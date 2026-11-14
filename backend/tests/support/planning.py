"""Moved test support."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.exc import DBAPIError

from app.domain.planning import (
    CapacityEntry,
    ConstraintSet,
    ConstraintSetInput,
    Dependency,
    DependencyCreate,
    Milestone,
    PoolEmployee,
)
from app.repositories.access import ProjectRef
from tests.support.store import ProjectScopedFake, Store


def p0001(message: str) -> DBAPIError:
    class _OrigError(Exception):
        sqlstate = "P0001"

    orig = _OrigError()
    orig.message = message  # type: ignore[attr-defined]
    return DBAPIError("INSERT ...", {}, orig)


@dataclass
class PlanStore:
    constraints: dict[uuid.UUID, ConstraintSet] = field(default_factory=dict)
    nodes: dict[uuid.UUID, tuple[uuid.UUID, str]] = field(default_factory=dict)
    deps: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    team: list[PoolEmployee] = field(default_factory=list)
    required: dict[uuid.UUID, tuple[str, int]] = field(default_factory=dict)
    held: dict[uuid.UUID, tuple[str, int]] = field(default_factory=dict)
    weeks: list[Sequence[date]] = field(default_factory=list)


class FakePlanning(ProjectScopedFake):
    def __init__(self, store: Store, ps: PlanStore, user_id: uuid.UUID) -> None:
        super().__init__(store, user_id)
        self.plans = ps

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        p = self.store.projects.get(project_id)
        if p is None or self._role(project_id) is None:
            return None
        return ProjectRef(p.id, p.org_id, p.name, p.start_date, p.target_end_date, p.budget_cents)

    async def constraints(self, project: ProjectRef) -> ConstraintSet:
        return self.plans.constraints.get(project.id) or ConstraintSet(
            budget_total_cents=project.budget_cents,
            contingency_pct=0,
            labor_cap_cents=None,
            infra_cap_cents=None,
            budget_mode="hard",
            start_date=project.start_date,
            must_finish_by=project.target_end_date,
            deadline_mode="hard",
            working_days_per_week=5,
            capacity_mode="soft",
            skill_mode="hard",
            milestones=[],
        )

    async def node_ids(self, project_id: uuid.UUID, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        return {i for i in ids if i in self.plans.nodes and self.plans.nodes[i][0] == project_id}

    async def save_constraints(self, project_id: uuid.UUID, body: ConstraintSetInput) -> None:
        previous = self.plans.constraints.get(project_id)
        self.plans.constraints[project_id] = ConstraintSet.model_validate(
            {
                **(previous.model_dump(by_alias=False) if previous else {}),
                **body.values(),
                "milestones": [
                    Milestone.model_validate(
                        {**m.model_dump(by_alias=False), "id": m.id or uuid.uuid4()}
                    )
                    for m in body.milestones
                ],
            }
        )

    async def capacity(self, project: ProjectRef, weeks: Sequence[date]) -> list[CapacityEntry]:
        self.plans.weeks.append(weeks)
        return [
            CapacityEntry.model_validate({**e.model_dump(by_alias=False), "allocated_hours": 7.5})
            for e in self.plans.team
        ]

    async def employees(self, org_id: uuid.UUID) -> list[PoolEmployee]:
        return self.plans.team

    async def required_skills(self, project_id: uuid.UUID) -> dict[uuid.UUID, tuple[str, int]]:
        return self.plans.required

    async def held_skills(self, org_id: uuid.UUID) -> dict[uuid.UUID, tuple[str, int]]:
        return self.plans.held

    def _label(self, node_id: uuid.UUID) -> str:
        return self.plans.nodes[node_id][1]

    async def dependencies(self, project_id: uuid.UUID) -> list[Dependency]:
        return [
            d
            for dep_id, row in self.plans.deps.items()
            if row["project_id"] == project_id and (d := await self.dependency(dep_id)) is not None
        ]

    async def dependency_project(self, dependency_id: uuid.UUID) -> uuid.UUID | None:
        row = self.plans.deps.get(dependency_id)
        if row is None or self._role(row["project_id"]) is None:
            return None
        return uuid.UUID(str(row["project_id"]))

    async def dependency(self, dependency_id: uuid.UUID) -> Dependency | None:
        row = self.plans.deps.get(dependency_id)
        if row is None:
            return None
        return Dependency.model_validate(
            {
                **row,
                "predecessor_label": self._label(row["predecessor_id"]),
                "successor_label": self._label(row["successor_id"]),
            }
        )

    async def create_dependency(self, project_id: uuid.UUID, body: DependencyCreate) -> uuid.UUID:
        for node in (body.predecessor_id, body.successor_id):
            if self.plans.nodes.get(node, (None, ""))[0] != project_id:
                raise p0001("Work package not found in this project")
        dep_id = uuid.uuid4()
        self.plans.deps[dep_id] = {
            "id": dep_id,
            "project_id": project_id,
            **body.model_dump(by_alias=False),
            "origin": "manual",
        }
        return dep_id

    async def update_dependency(self, dependency_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        self.plans.deps[dependency_id].update(changes)
        return True

    async def delete_dependency(self, dependency_id: uuid.UUID) -> bool:
        return self.plans.deps.pop(dependency_id, None) is not None


CONSTRAINTS: dict[str, Any] = {
    "budgetTotalCents": 4_000_000,
    "contingencyPct": 12.5,
    "laborCapCents": None,
    "infraCapCents": 100_000,
    "budgetMode": "soft",
    "startDate": "2026-11-16",
    "mustFinishBy": "2026-12-31",
    "deadlineMode": "hard",
    "workingDaysPerWeek": 5,
    "capacityMode": "soft",
    "skillMode": "hard",
    "milestones": [{"name": "Go-live", "dueDate": "2026-12-15", "mode": "hard"}],
}
