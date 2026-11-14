"""Change requests (step 7), under the caller's RLS session."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.changes.context import ChangeContext, NodeRow, PersonRef, PlanRow, RunMeta, TaskRef
from app.changes.derive import derive_overrides
from app.domain.changes import (
    ChangeImpact,
    ChangeRequest,
    ChangeSource,
    ChangeStatus,
    PlanOverrides,
)
from app.domain.common import SolverStatus
from app.repositories import access
from app.repositories.access import ProjectRef
from app.repositories.tables import (
    change_requests,
    constraint_sets,
    dependencies,
    employees,
    optimization_runs,
    planned_assignments,
    project_team_members,
    projects,
    wbs_nodes,
    wbs_nodes_with_code,
)

SUCCESS = (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
cr = change_requests
r = optimization_runs


@dataclass(frozen=True, slots=True)
class InForce:
    run_id: uuid.UUID
    version: int | None
    expected_cost_cents: int | None
    completion_date: date | None


@dataclass(frozen=True, slots=True)
class RunState:
    id: uuid.UUID
    project_id: uuid.UUID
    status: SolverStatus
    applied: bool
    version: int | None


def to_change(m: Any) -> ChangeRequest:
    """Row (with base_version / proposed_run_status joined in) -> API model."""
    impact = ChangeImpact.model_validate(m["impact"]) if m["impact"] else None
    answers = dict(m["answers"] or {})
    status = ChangeStatus(m["status"])
    run_status = SolverStatus(m["proposed_run_status"]) if m["proposed_run_status"] else None
    if status == ChangeStatus.REPLANNING and run_status in SUCCESS:
        status = ChangeStatus.PROPOSED
    return ChangeRequest(
        id=m["id"],
        project_id=m["project_id"],
        source=m["source"],
        input_text=m["input_text"],
        status=status,
        impact=impact,
        answers=answers,
        base_run_id=m["base_run_id"],
        base_version=m["base_version"],
        proposed_run_id=m["proposed_run_id"],
        proposed_run_status=run_status,
        error=m["error"],
        created_at=m["created_at"],
        decided_at=m["decided_at"],
        overrides=derive_overrides(impact, answers) if impact else None,
    )


def _select() -> sa.Select[Any]:
    base = r.alias("base_run")
    prop = r.alias("proposed_run")
    return sa.select(
        cr.c.id,
        cr.c.project_id,
        cr.c.source,
        cr.c.input_text,
        cr.c.status,
        cr.c.impact,
        cr.c.answers,
        cr.c.base_run_id,
        cr.c.proposed_run_id,
        cr.c.error,
        cr.c.created_at,
        cr.c.decided_at,
        base.c.version.label("base_version"),
        prop.c.status.label("proposed_run_status"),
    ).select_from(
        cr.outerjoin(base, base.c.id == cr.c.base_run_id).outerjoin(
            prop, prop.c.id == cr.c.proposed_run_id
        )
    )


class ChangeRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        return await access.visible_project(self._conn, project_id)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return await access.can_write_project(self._conn, project_id)

    # ------------------------------------------------------------------ plan in force
    async def in_force(self, project_id: uuid.UUID) -> InForce | None:
        """The plan in force: the run applied last."""
        stmt = (
            sa.select(r.c.id, r.c.version, r.c.expected_cost_cents, r.c.completion_date)
            .where(r.c.project_id == project_id, r.c.applied_at.is_not(None))
            .order_by(r.c.applied_at.desc(), r.c.version.desc().nulls_last())
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        return InForce(row.id, row.version, row.expected_cost_cents, row.completion_date)

    async def run(self, run_id: uuid.UUID) -> RunState | None:
        stmt = sa.select(r.c.id, r.c.project_id, r.c.status, r.c.applied_at, r.c.version).where(
            r.c.id == run_id
        )
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        return RunState(
            row.id,
            row.project_id,
            SolverStatus(row.status),
            row.applied_at is not None,
            row.version,
        )

    async def run_meta(self, run_id: uuid.UUID) -> RunMeta | None:
        stmt = sa.select(
            r.c.id,
            r.c.project_id,
            r.c.status,
            r.c.version,
            r.c.expected_cost_cents,
            r.c.completion_date,
            r.c.input_snapshot,
        ).where(r.c.id == run_id)
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        return RunMeta(
            row.id,
            row.project_id,
            SolverStatus(row.status),
            row.version,
            row.expected_cost_cents,
            row.completion_date,
            dict(row.input_snapshot or {}),
        )

    async def plan_rows(self, run_id: uuid.UUID) -> list[PlanRow]:
        pa = planned_assignments
        stmt = (
            sa.select(
                pa.c.wbs_node_id,
                pa.c.employee_id,
                pa.c.contractor_label,
                pa.c.start_week,
                pa.c.end_week,
            )
            .where(pa.c.run_id == run_id)
            .order_by(pa.c.start_week, pa.c.wbs_node_id)
        )
        return [
            PlanRow(
                row.wbs_node_id,
                row.employee_id,
                row.contractor_label,
                row.start_week,
                row.end_week,
            )
            for row in (await self._conn.execute(stmt)).all()
        ]

    async def nodes(self, project_id: uuid.UUID) -> list[NodeRow]:
        w = wbs_nodes_with_code
        stmt = (
            sa.select(w.c.id, w.c.parent_id, w.c.level, w.c.code, w.c.name)
            .where(w.c.project_id == project_id)
            .order_by(w.c.path)
        )
        return [
            NodeRow(row.id, row.parent_id, row.level, row.code, row.name)
            for row in (await self._conn.execute(stmt)).all()
        ]

    async def employee_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        e = employees
        rows = (
            await self._conn.execute(sa.select(e.c.id, e.c.full_name).where(e.c.id.in_(ids)))
        ).all()
        return {row.id: row.full_name for row in rows}

    # ------------------------------------------------------------------ context
    async def context(
        self, project: ProjectRef, in_force: InForce | None, today: date
    ) -> ChangeContext:
        cs = constraint_sets
        row = (
            await self._conn.execute(
                sa.select(cs.c.budget_total_cents, cs.c.must_finish_by, cs.c.start_date).where(
                    cs.c.project_id == project.id
                )
            )
        ).first()
        budget = row.budget_total_cents if row and row.budget_total_cents is not None else None
        deadline = row.must_finish_by if row and row.must_finish_by else None
        start = row.start_date if row and row.start_date else None

        w = wbs_nodes_with_code
        nodes = (
            await self._conn.execute(
                sa.select(
                    w.c.id,
                    w.c.parent_id,
                    w.c.level,
                    w.c.code,
                    w.c.name,
                    w.c.duration_days,
                    w.c.owner_employee_id,
                )
                .where(w.c.project_id == project.id)
                .order_by(w.c.path)
            )
        ).all()
        children: dict[uuid.UUID, int] = defaultdict(int)
        for n in nodes:
            if n.parent_id is not None:
                children[n.parent_id] += 1
        tasks = tuple(
            TaskRef(n.id, n.code, n.name, n.duration_days, n.owner_employee_id)
            for n in nodes
            if n.level == 3 or (n.level == 2 and not children.get(n.id))
        )

        d = dependencies
        edges = tuple(
            (row.predecessor_id, row.successor_id)
            for row in (
                await self._conn.execute(
                    sa.select(d.c.predecessor_id, d.c.successor_id).where(
                        d.c.project_id == project.id
                    )
                )
            ).all()
        )
        return ChangeContext(
            budget_cents=budget if budget is not None else project.budget_cents,
            deadline=deadline or project.target_end_date,
            start=start or project.start_date,
            tasks=tasks,
            people=await self._people(project),
            edges=edges,
            plan_version=in_force.version if in_force else None,
            plan_cost_cents=in_force.expected_cost_cents if in_force else None,
            plan_finish=in_force.completion_date if in_force else None,
            today=today,
        )

    async def _people(self, project: ProjectRef) -> tuple[PersonRef, ...]:
        """The project's team; everyone active in the org when no team is set (as the optimizer)."""
        e, tm = employees, project_team_members
        rows = (
            await self._conn.execute(
                sa.select(e.c.id, e.c.full_name, tm.c.hours_per_week)
                .select_from(tm.join(e, e.c.id == tm.c.employee_id))
                .where(tm.c.project_id == project.id)
                .order_by(e.c.full_name)
            )
        ).all()
        if not rows:
            rows = (
                await self._conn.execute(
                    sa.select(
                        e.c.id, e.c.full_name, e.c.weekly_capacity_hours.label("hours_per_week")
                    )
                    .where(e.c.org_id == project.org_id, e.c.active.is_(True))
                    .order_by(e.c.full_name)
                )
            ).all()
        return tuple(PersonRef(row.id, row.full_name, float(row.hours_per_week)) for row in rows)

    # ------------------------------------------------------------------ change requests
    async def create(
        self,
        project_id: uuid.UUID,
        source: ChangeSource,
        input_text: str | None,
        impact: ChangeImpact,
        base_run_id: uuid.UUID,
    ) -> uuid.UUID:
        change_id = uuid.uuid4()
        await self._conn.execute(
            sa.insert(cr).values(
                id=change_id,
                project_id=project_id,
                source=source,
                input_text=input_text,
                status=ChangeStatus.READY,
                impact=impact.model_dump(mode="json"),
                base_run_id=base_run_id,
            )
        )
        return change_id

    async def get(self, change_id: uuid.UUID) -> ChangeRequest | None:
        row = (await self._conn.execute(_select().where(cr.c.id == change_id))).first()
        return to_change(row._mapping) if row else None

    async def list(self, project_id: uuid.UUID) -> list[ChangeRequest]:
        stmt = (
            _select()
            .where(cr.c.project_id == project_id)
            .order_by(cr.c.created_at.desc(), cr.c.id.desc())
            .limit(100)
        )
        return [to_change(row._mapping) for row in (await self._conn.execute(stmt)).all()]

    async def save_answers(
        self, change_id: uuid.UUID, answers: dict[str, str], *, reset_proposal: bool
    ) -> None:
        values: dict[str, Any] = {"answers": answers}
        if reset_proposal:
            values |= {"status": ChangeStatus.READY, "proposed_run_id": None}
        await self._conn.execute(sa.update(cr).where(cr.c.id == change_id).values(**values))

    async def decide(
        self, change_id: uuid.UUID, status: ChangeStatus, proposed_run_id: uuid.UUID | None
    ) -> None:
        values: dict[str, Any] = {"status": status, "decided_at": sa.func.now()}
        if proposed_run_id is not None:
            values["proposed_run_id"] = proposed_run_id
        await self._conn.execute(sa.update(cr).where(cr.c.id == change_id).values(**values))

    # ------------------------------------------------------------------ approve
    async def apply_run(self, run_id: uuid.UUID) -> None:
        """Plan vN+1 goes into force (BE1's function assigns the version)."""
        await self._conn.execute(sa.select(sa.func.public.apply_optimization_run(run_id)))

    async def write_overrides(self, project: ProjectRef, o: PlanOverrides) -> None:
        """The approved change becomes the project's inputs for every later run."""
        if o.budget_total_cents is not None or o.must_finish_by is not None:
            cs = constraint_sets
            changes: dict[str, Any] = {"updated_at": sa.func.now()}
            if o.budget_total_cents is not None:
                changes["budget_total_cents"] = o.budget_total_cents
            if o.must_finish_by is not None:
                changes["must_finish_by"] = o.must_finish_by
            stmt = pg_insert(cs).values(
                project_id=project.id,
                budget_total_cents=(
                    o.budget_total_cents
                    if o.budget_total_cents is not None
                    else project.budget_cents
                ),
                start_date=project.start_date,
                must_finish_by=o.must_finish_by or project.target_end_date,
            )
            await self._conn.execute(
                stmt.on_conflict_do_update(index_elements=[cs.c.project_id], set_=changes)
            )
            p = projects
            mirror: dict[str, Any] = {}
            if o.budget_total_cents is not None:
                mirror["budget_cents"] = o.budget_total_cents
            if o.must_finish_by is not None:
                mirror["target_end_date"] = o.must_finish_by
            await self._conn.execute(sa.update(p).where(p.c.id == project.id).values(**mirror))
        for node_id, days in (o.duration_days or {}).items():
            n = wbs_nodes
            await self._conn.execute(
                sa.update(n)
                .where(n.c.id == uuid.UUID(node_id), n.c.project_id == project.id)
                .values(
                    duration_days=days,
                    estimate_confirmed_at=sa.func.now(),
                    estimate_confirmed_by=sa.func.auth.uid(),
                )
            )
        tm = project_team_members
        for employee_id, hours in (o.hours_per_week or {}).items():
            where = (tm.c.project_id == project.id, tm.c.employee_id == uuid.UUID(employee_id))
            if hours <= 0:
                await self._conn.execute(sa.delete(tm).where(*where))
            else:
                await self._conn.execute(
                    sa.update(tm).where(*where).values(hours_per_week=max(hours, 1))
                )
