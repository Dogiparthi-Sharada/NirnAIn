"""Read-only project context for the planning assistant (caller's RLS session).

Purpose: Give the assistant the facts of the project the user is looking at (accepted scope,
WBS, constraints, the latest run and its recommendations) and nothing the user could not see.
How it fits: Used by app/api/v1/assistant.py; builds `app.ai.assistant.ProjectContext`, which
the prompt is rendered from; layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.assistant import ProjectContext, WbsLine, kpi_lines, money
from app.domain.common import ScopeStatus
from app.repositories.tables import (
    constraint_sets,
    employees,
    optimization_runs,
    projects,
    recommendations,
    scope_items,
    wbs_nodes_with_code,
)

MODE = {"hard": "Must hold", "soft": "Can flex"}
MAX_RECOMMENDATIONS = 5


def _constraints(row: Any) -> tuple[str, ...]:
    """Describe a constraint set row as short lines with each policy in words."""
    out: list[str] = []
    if row.budget_total_cents is not None:
        out.append(f"Budget: {money(row.budget_total_cents)} ({MODE.get(row.budget_mode, '-')})")
    if row.must_finish_by is not None:
        out.append(f"Must finish by {row.must_finish_by} ({MODE.get(row.deadline_mode, '-')})")
    if row.start_date is not None:
        out.append(f"Planning starts {row.start_date}")
    out.append(f"Working days per week: {row.working_days_per_week}")
    out.append(f"Team capacity: {MODE.get(row.capacity_mode, '-')}")
    out.append(f"Skills: {MODE.get(row.skill_mode, '-')}")
    return tuple(out)


class AssistantRepository:
    """Reads the project facts the assistant may talk about."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def context(self, project_id: uuid.UUID) -> ProjectContext | None:
        """Return the project as the caller can see it, or None when it isn't visible."""
        project = (
            await self._conn.execute(
                sa.select(
                    projects.c.name,
                    projects.c.description,
                    projects.c.status,
                    projects.c.current_step,
                    projects.c.start_date,
                    projects.c.target_end_date,
                    projects.c.budget_cents,
                ).where(projects.c.id == project_id)
            )
        ).first()
        if project is None:
            return None
        scope = await self._accepted_scope(project_id)
        wbs = await self._wbs_lines(project_id)
        constraint_row = (
            await self._conn.execute(
                sa.select(constraint_sets).where(constraint_sets.c.project_id == project_id)
            )
        ).first()
        run_status, kpis, reasons = await self._latest_run(project_id)
        return ProjectContext(
            name=project.name,
            description=project.description,
            status=str(project.status),
            current_step=str(project.current_step),
            start_date=project.start_date,
            target_end_date=project.target_end_date,
            budget_cents=project.budget_cents,
            scope=scope,
            wbs=wbs,
            constraints=_constraints(constraint_row) if constraint_row else (),
            run_status=run_status,
            run_kpis=kpis,
            recommendations=reasons,
        )

    async def _accepted_scope(self, project_id: uuid.UUID) -> dict[str, list[str]]:
        """Return the accepted scope item texts by category."""
        scope: dict[str, list[str]] = defaultdict(list)
        for row in (
            await self._conn.execute(
                sa.select(scope_items.c.category, scope_items.c.text)
                .where(
                    scope_items.c.project_id == project_id,
                    scope_items.c.status == ScopeStatus.ACCEPTED,
                )
                .order_by(scope_items.c.category, scope_items.c.sort_order)
            )
        ).all():
            scope[str(row.category)].append(row.text)
        return dict(scope)

    async def _wbs_lines(self, project_id: uuid.UUID) -> tuple[WbsLine, ...]:
        """Return the WBS in tree order with durations and owner names."""
        view, owners = wbs_nodes_with_code, employees
        return tuple(
            WbsLine(row.code, row.name, row.level, row.duration_days, row.owner)
            for row in (
                await self._conn.execute(
                    sa.select(
                        view.c.code,
                        view.c.name,
                        view.c.level,
                        view.c.duration_days,
                        owners.c.full_name.label("owner"),
                    )
                    .select_from(view.outerjoin(owners, owners.c.id == view.c.owner_employee_id))
                    .where(view.c.project_id == project_id)
                    .order_by(view.c.path)
                )
            ).all()
        )

    async def _latest_run(
        self, project_id: uuid.UUID
    ) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
        """Return the latest run's status, KPI lines and top recommendation reasons."""
        runs = optimization_runs
        run = (
            await self._conn.execute(
                sa.select(runs.c.id, runs.c.status, runs.c.result, runs.c.version)
                .where(runs.c.project_id == project_id)
                .order_by(runs.c.created_at.desc())
                .limit(1)
            )
        ).first()
        if run is None:
            return None, (), ()
        run_status = str(run.status) + (f" (applied as Plan v{run.version})" if run.version else "")
        result = run.result if isinstance(run.result, dict) else {}
        kpis = kpi_lines(result.get("kpis") or [])
        reasons = tuple(
            str(row.reason)
            for row in (
                await self._conn.execute(
                    sa.select(recommendations.c.reason)
                    .where(recommendations.c.run_id == run.id)
                    .order_by(recommendations.c.sort_order)
                    .limit(MAX_RECOMMENDATIONS)
                )
            ).all()
        )
        return run_status, kpis, reasons
