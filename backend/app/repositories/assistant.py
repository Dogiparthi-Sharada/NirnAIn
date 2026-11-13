"""Read-only project context for the planning assistant (caller's RLS session)."""

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


def _constraints(row: Any) -> tuple[str, ...]:
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
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def context(self, project_id: uuid.UUID) -> ProjectContext | None:
        """The project as the caller can see it, or None when it isn't visible."""
        p = projects
        project = (
            await self._conn.execute(
                sa.select(
                    p.c.name,
                    p.c.description,
                    p.c.status,
                    p.c.current_step,
                    p.c.start_date,
                    p.c.target_end_date,
                    p.c.budget_cents,
                ).where(p.c.id == project_id)
            )
        ).first()
        if project is None:
            return None

        s = scope_items
        scope: dict[str, list[str]] = defaultdict(list)
        for row in (
            await self._conn.execute(
                sa.select(s.c.category, s.c.text)
                .where(s.c.project_id == project_id, s.c.status == ScopeStatus.ACCEPTED)
                .order_by(s.c.category, s.c.sort_order)
            )
        ).all():
            scope[str(row.category)].append(row.text)

        w, e = wbs_nodes_with_code, employees
        wbs = tuple(
            WbsLine(r.code, r.name, r.level, r.duration_days, r.owner)
            for r in (
                await self._conn.execute(
                    sa.select(
                        w.c.code,
                        w.c.name,
                        w.c.level,
                        w.c.duration_days,
                        e.c.full_name.label("owner"),
                    )
                    .select_from(w.outerjoin(e, e.c.id == w.c.owner_employee_id))
                    .where(w.c.project_id == project_id)
                    .order_by(w.c.path)
                )
            ).all()
        )

        c = constraint_sets
        crow = (await self._conn.execute(sa.select(c).where(c.c.project_id == project_id))).first()

        r = optimization_runs
        run = (
            await self._conn.execute(
                sa.select(r.c.id, r.c.status, r.c.result, r.c.version)
                .where(r.c.project_id == project_id)
                .order_by(r.c.created_at.desc())
                .limit(1)
            )
        ).first()
        run_status: str | None = None
        kpis: tuple[str, ...] = ()
        recs: tuple[str, ...] = ()
        if run is not None:
            run_status = str(run.status) + (
                f" (applied as Plan v{run.version})" if run.version else ""
            )
            result = run.result if isinstance(run.result, dict) else {}
            kpis = kpi_lines(result.get("kpis") or [])
            rec = recommendations
            recs = tuple(
                str(x.reason)
                for x in (
                    await self._conn.execute(
                        sa.select(rec.c.reason)
                        .where(rec.c.run_id == run.id)
                        .order_by(rec.c.sort_order)
                        .limit(5)
                    )
                ).all()
            )

        return ProjectContext(
            name=project.name,
            description=project.description,
            status=str(project.status),
            current_step=str(project.current_step),
            start_date=project.start_date,
            target_end_date=project.target_end_date,
            budget_cents=project.budget_cents,
            scope=dict(scope),
            wbs=wbs,
            constraints=_constraints(crow) if crow else (),
            run_status=run_status,
            run_kpis=kpis,
            recommendations=recs,
        )
