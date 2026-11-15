"""Dependency links between a project's work packages (caller's RLS session).

Purpose: Read and change the finish/start links the optimizer schedules by, labelled with the WBS
codes the PM recognises and ordered as the WBS is.
How it fits: Mixed into `PlanningRepository` (app/repositories/planning.py) and used through it by
app/api/v1/planning.py; the cycle check lives in a database trigger; layer: repositories.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.planning import Dependency, DependencyCreate
from app.repositories.tables import dependencies, wbs_nodes_with_code


@dataclass(frozen=True, slots=True)
class _NodeLabel:
    """A WBS node's position (for ordering) and its `code name` label."""

    path: list[int]
    label: str


class DependencyQueries:
    """Dependency reads and writes, mixed into `PlanningRepository` (caller's RLS session)."""

    _conn: AsyncConnection

    async def _labels(self, project_id: uuid.UUID) -> dict[uuid.UUID, _NodeLabel]:
        """Return each WBS node's path and `code name` label."""
        view = wbs_nodes_with_code
        rows = (
            await self._conn.execute(
                sa.select(view.c.id, view.c.code, view.c.name, view.c.path).where(
                    view.c.project_id == project_id
                )
            )
        ).all()
        return {row.id: _NodeLabel(list(row.path), f"{row.code} {row.name}") for row in rows}

    @staticmethod
    def _to_dependency(row: Any, labels: dict[uuid.UUID, _NodeLabel]) -> Dependency:
        """Turn a dependency row into the API model with both ends labelled."""
        missing = _NodeLabel([], "")
        return Dependency.model_validate(
            {
                **row._mapping,
                "predecessor_label": labels.get(row.predecessor_id, missing).label,
                "successor_label": labels.get(row.successor_id, missing).label,
            }
        )

    def _dependency_select(self) -> sa.Select[Any]:
        """Return the select of dependency columns."""
        links = dependencies
        return sa.select(
            links.c.id,
            links.c.project_id,
            links.c.predecessor_id,
            links.c.successor_id,
            links.c.type,
            links.c.lag_days,
            links.c.origin,
        )

    async def dependencies(self, project_id: uuid.UUID) -> list[Dependency]:
        """List the project's dependencies in WBS order of predecessor, then successor."""
        links = dependencies
        rows = (
            await self._conn.execute(
                self._dependency_select()
                .where(links.c.project_id == project_id)
                .order_by(links.c.created_at, links.c.id)
            )
        ).all()
        labels = await self._labels(project_id)
        missing = _NodeLabel([], "")
        rows = sorted(
            rows,
            key=lambda row: (
                labels.get(row.predecessor_id, missing).path,
                labels.get(row.successor_id, missing).path,
            ),
        )
        return [self._to_dependency(row, labels) for row in rows]

    async def dependency_project(self, dependency_id: uuid.UUID) -> uuid.UUID | None:
        """Return the project a dependency belongs to, or None when it is missing or hidden."""
        links = dependencies
        stmt = sa.select(links.c.project_id).where(links.c.id == dependency_id)
        return (await self._conn.execute(stmt)).scalar()

    async def dependency(self, dependency_id: uuid.UUID) -> Dependency | None:
        """Return one dependency with its labels, or None."""
        links = dependencies
        row = (
            await self._conn.execute(self._dependency_select().where(links.c.id == dependency_id))
        ).first()
        if row is None:
            return None
        return self._to_dependency(row, await self._labels(row.project_id))

    async def create_dependency(self, project_id: uuid.UUID, body: DependencyCreate) -> uuid.UUID:
        """Insert a dependency and return its id (the database rejects links that close a cycle)."""
        dependency_id = uuid.uuid4()
        await self._conn.execute(
            sa.insert(dependencies).values(
                id=dependency_id,
                project_id=project_id,
                predecessor_id=body.predecessor_id,
                successor_id=body.successor_id,
                type=body.type,
                lag_days=body.lag_days,
            )
        )
        return dependency_id

    async def update_dependency(self, dependency_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        """Change a dependency's type or lag; False when nothing was updated."""
        links = dependencies
        result = await self._conn.execute(
            sa.update(links).where(links.c.id == dependency_id).values(**changes)
        )
        return result.rowcount > 0

    async def delete_dependency(self, dependency_id: uuid.UUID) -> bool:
        """Delete a dependency; False when nothing was deleted."""
        links = dependencies
        result = await self._conn.execute(sa.delete(links).where(links.c.id == dependency_id))
        return result.rowcount > 0
