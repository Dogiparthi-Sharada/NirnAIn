"""Constraints, resource capacity, skills coverage and dependencies of a project."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.common import ConstraintMode
from app.domain.planning import (
    CapacityEntry,
    ConstraintSet,
    ConstraintSetInput,
    Dependency,
    DependencyCreate,
    EmployeeSkill,
    Milestone,
    PoolEmployee,
    average_weekly_hours,
)
from app.repositories import access
from app.repositories.access import ProjectRef
from app.repositories.tables import (
    allocations,
    constraint_sets,
    dependencies,
    employee_skills,
    employees,
    milestones,
    skills,
    task_skill_requirements,
    wbs_nodes,
    wbs_nodes_with_code,
)

SkillLevels = dict[uuid.UUID, tuple[str, int]]


@dataclass(frozen=True, slots=True)
class _NodeLabel:
    path: list[int]
    label: str


def _default_constraints(project: ProjectRef) -> dict[str, Any]:
    """What GET returns before the project has a constraint set (table defaults)."""
    return {
        "budget_total_cents": project.budget_cents,
        "contingency_pct": 0.0,
        "labor_cap_cents": None,
        "infra_cap_cents": None,
        "budget_mode": ConstraintMode.HARD,
        "start_date": project.start_date,
        "must_finish_by": project.target_end_date,
        "deadline_mode": ConstraintMode.HARD,
        "working_days_per_week": 5,
        "capacity_mode": ConstraintMode.SOFT,
        "skill_mode": ConstraintMode.HARD,
        "specialist_answer": None,
        "deadline_answer": None,
        "specialist_rate_confirmed": False,
        "contractor_rate_cents": None,
        "allow_contractors": True,
    }


class PlanningRepository:
    """Planning resources. Visibility and write permissions come from RLS."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        return await access.visible_project(self._conn, project_id)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return await access.can_write_project(self._conn, project_id)

    # ------------------------------------------------------------------ constraints
    async def constraints(self, project: ProjectRef) -> ConstraintSet:
        cs = constraint_sets
        row = (
            await self._conn.execute(
                sa.select(
                    cs.c.budget_total_cents,
                    cs.c.contingency_pct,
                    cs.c.labor_cap_cents,
                    cs.c.infra_cap_cents,
                    cs.c.budget_mode,
                    cs.c.start_date,
                    cs.c.must_finish_by,
                    cs.c.deadline_mode,
                    cs.c.working_days_per_week,
                    cs.c.capacity_mode,
                    cs.c.skill_mode,
                    cs.c.specialist_answer,
                    cs.c.deadline_answer,
                    cs.c.specialist_rate_confirmed,
                    cs.c.contractor_rate_cents,
                    cs.c.allow_contractors,
                ).where(cs.c.project_id == project.id)
            )
        ).first()
        values = dict(row._mapping) if row else _default_constraints(project)
        ms = milestones
        rows = (
            await self._conn.execute(
                sa.select(ms.c.id, ms.c.name, ms.c.due_date, ms.c.wbs_node_id, ms.c.mode)
                .where(ms.c.project_id == project.id)
                .order_by(ms.c.due_date, ms.c.created_at, ms.c.id)
            )
        ).all()
        return ConstraintSet.model_validate(
            {**values, "milestones": [Milestone.model_validate(dict(r._mapping)) for r in rows]}
        )

    async def node_ids(self, project_id: uuid.UUID, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """The subset of `ids` that are WBS nodes of the project."""
        if not ids:
            return set()
        n = wbs_nodes
        stmt = sa.select(n.c.id).where(n.c.project_id == project_id, n.c.id.in_(ids))
        return set((await self._conn.execute(stmt)).scalars())

    async def save_constraints(self, project_id: uuid.UUID, body: ConstraintSetInput) -> None:
        values = body.values()
        values["contingency_pct"] = Decimal(str(round(values["contingency_pct"], 2)))
        cs = constraint_sets
        await self._conn.execute(
            pg_insert(cs)
            .values(project_id=project_id, **values)
            .on_conflict_do_update(index_elements=[cs.c.project_id], set_=values)
        )

        ms = milestones
        existing: set[uuid.UUID] = set(
            (
                await self._conn.execute(sa.select(ms.c.id).where(ms.c.project_id == project_id))
            ).scalars()
        )
        keep = {m.id for m in body.milestones if m.id in existing}
        await self._conn.execute(
            sa.delete(ms).where(ms.c.project_id == project_id, ms.c.id.not_in(keep))
            if keep
            else sa.delete(ms).where(ms.c.project_id == project_id)
        )
        for m in body.milestones:
            fields = {
                "name": m.name,
                "due_date": m.due_date,
                "wbs_node_id": m.wbs_node_id,
                "mode": m.mode,
            }
            if m.id in keep:
                await self._conn.execute(
                    sa.update(ms)
                    .where(ms.c.id == m.id, ms.c.project_id == project_id)
                    .values(fields)
                )
            else:
                await self._conn.execute(
                    sa.insert(ms).values(id=m.id or uuid.uuid4(), project_id=project_id, **fields)
                )

    # ------------------------------------------------------------------ resources
    def _pool_select(self, org_id: uuid.UUID) -> sa.Select[Any]:
        e = employees
        return (
            sa.select(
                e.c.id.label("employee_id"),
                e.c.full_name,
                e.c.role_title,
                e.c.weekly_capacity_hours,
                e.c.hourly_rate_cents,
                e.c.is_contractor,
            )
            .where(e.c.org_id == org_id, e.c.active.is_(True))
            .order_by(e.c.full_name, e.c.id)
        )

    async def _skills_of(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[EmployeeSkill]]:
        out: dict[uuid.UUID, list[EmployeeSkill]] = defaultdict(list)
        if ids:
            es, s = employee_skills, skills
            for row in (
                await self._conn.execute(
                    sa.select(es.c.employee_id, es.c.skill_id, s.c.name, es.c.proficiency)
                    .select_from(es.join(s, s.c.id == es.c.skill_id))
                    .where(es.c.employee_id.in_(ids))
                    .order_by(es.c.employee_id, s.c.name)
                )
            ).all():
                out[row.employee_id].append(
                    EmployeeSkill(skill_id=row.skill_id, name=row.name, proficiency=row.proficiency)
                )
        return out

    async def employees(self, org_id: uuid.UUID) -> list[PoolEmployee]:
        rows = (await self._conn.execute(self._pool_select(org_id))).all()
        by_emp = await self._skills_of([r.employee_id for r in rows])
        return [
            PoolEmployee.model_validate({**r._mapping, "skills": by_emp.get(r.employee_id, [])})
            for r in rows
        ]

    async def capacity(self, project: ProjectRef, weeks: Sequence[date]) -> list[CapacityEntry]:
        """Active org employees with their average weekly hours on other projects in `weeks`."""
        e, al = employees, allocations
        other = (
            sa.select(al.c.employee_id, sa.func.sum(al.c.hours).label("total"))
            .where(
                al.c.project_id != project.id,
                al.c.week_start >= min(weeks),
                al.c.week_start <= max(weeks),
                al.c.employee_id.in_(sa.select(e.c.id).where(e.c.org_id == project.org_id)),
            )
            .group_by(al.c.employee_id)
            .subquery()
        )
        stmt = (
            self._pool_select(project.org_id)
            .add_columns(sa.func.coalesce(other.c.total, 0).label("total"))
            .outerjoin(other, other.c.employee_id == e.c.id)
        )
        rows = (await self._conn.execute(stmt)).all()
        by_emp = await self._skills_of([r.employee_id for r in rows])
        return [
            CapacityEntry.model_validate(
                {
                    **{k: v for k, v in r._mapping.items() if k != "total"},
                    "allocated_hours": average_weekly_hours(float(r.total), len(weeks)),
                    "skills": by_emp.get(r.employee_id, []),
                }
            )
            for r in rows
        ]

    async def required_skills(self, project_id: uuid.UUID) -> SkillLevels:
        t, s, n = task_skill_requirements, skills, wbs_nodes
        rows = (
            await self._conn.execute(
                sa.select(t.c.skill_id, s.c.name, sa.func.max(t.c.min_proficiency).label("level"))
                .select_from(t.join(n, n.c.id == t.c.wbs_node_id).join(s, s.c.id == t.c.skill_id))
                .where(n.c.project_id == project_id)
                .group_by(t.c.skill_id, s.c.name)
            )
        ).all()
        return {r.skill_id: (str(r.name), int(r.level)) for r in rows}

    async def held_skills(self, org_id: uuid.UUID) -> SkillLevels:
        es, e, s = employee_skills, employees, skills
        rows = (
            await self._conn.execute(
                sa.select(es.c.skill_id, s.c.name, sa.func.max(es.c.proficiency).label("level"))
                .select_from(
                    es.join(e, e.c.id == es.c.employee_id).join(s, s.c.id == es.c.skill_id)
                )
                .where(e.c.org_id == org_id, e.c.active.is_(True))
                .group_by(es.c.skill_id, s.c.name)
            )
        ).all()
        return {r.skill_id: (str(r.name), int(r.level)) for r in rows}

    # ------------------------------------------------------------------ dependencies
    async def _labels(self, project_id: uuid.UUID) -> dict[uuid.UUID, _NodeLabel]:
        w = wbs_nodes_with_code
        rows = (
            await self._conn.execute(
                sa.select(w.c.id, w.c.code, w.c.name, w.c.path).where(w.c.project_id == project_id)
            )
        ).all()
        return {r.id: _NodeLabel(list(r.path), f"{r.code} {r.name}") for r in rows}

    @staticmethod
    def _to_dependency(row: Any, labels: dict[uuid.UUID, _NodeLabel]) -> Dependency:
        missing = _NodeLabel([], "")
        return Dependency.model_validate(
            {
                **row._mapping,
                "predecessor_label": labels.get(row.predecessor_id, missing).label,
                "successor_label": labels.get(row.successor_id, missing).label,
            }
        )

    def _dependency_select(self) -> sa.Select[Any]:
        d = dependencies
        return sa.select(
            d.c.id,
            d.c.project_id,
            d.c.predecessor_id,
            d.c.successor_id,
            d.c.type,
            d.c.lag_days,
            d.c.origin,
        )

    async def dependencies(self, project_id: uuid.UUID) -> list[Dependency]:
        d = dependencies
        rows = (
            await self._conn.execute(
                self._dependency_select()
                .where(d.c.project_id == project_id)
                .order_by(d.c.created_at, d.c.id)
            )
        ).all()
        labels = await self._labels(project_id)
        missing = _NodeLabel([], "")
        rows = sorted(
            rows,
            key=lambda r: (
                labels.get(r.predecessor_id, missing).path,
                labels.get(r.successor_id, missing).path,
            ),
        )
        return [self._to_dependency(r, labels) for r in rows]

    async def dependency_project(self, dependency_id: uuid.UUID) -> uuid.UUID | None:
        d = dependencies
        stmt = sa.select(d.c.project_id).where(d.c.id == dependency_id)
        return (await self._conn.execute(stmt)).scalar()

    async def dependency(self, dependency_id: uuid.UUID) -> Dependency | None:
        d = dependencies
        row = (
            await self._conn.execute(self._dependency_select().where(d.c.id == dependency_id))
        ).first()
        if row is None:
            return None
        return self._to_dependency(row, await self._labels(row.project_id))

    async def create_dependency(self, project_id: uuid.UUID, body: DependencyCreate) -> uuid.UUID:
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
        d = dependencies
        result = await self._conn.execute(
            sa.update(d).where(d.c.id == dependency_id).values(**changes)
        )
        return result.rowcount > 0

    async def delete_dependency(self, dependency_id: uuid.UUID) -> bool:
        d = dependencies
        result = await self._conn.execute(sa.delete(d).where(d.c.id == dependency_id))
        return result.rowcount > 0
