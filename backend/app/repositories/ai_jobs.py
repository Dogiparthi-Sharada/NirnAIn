"""AI jobs (WBS generate / validate).

`AiJobRepository` runs under the caller's RLS session (queue and read jobs);
`AiJobStore` is the worker's service connection: it reads the project's
planning data and writes the generated WBS and the job outcome.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.wbs_generate import (
    MIN_PROFICIENCY,
    GenerationInput,
    ScopeEntry,
    TeamMember,
    WbsPlan,
)
from app.ai.wbs_validate import DependencyInfo, NodeInfo, ValidationInput
from app.core.errors import pg_error_info
from app.domain.ai import AiJob
from app.domain.common import AiJobKind, ItemOrigin, JobStatus
from app.repositories import access
from app.repositories.tables import (
    ai_jobs,
    constraint_sets,
    dependencies,
    employee_skills,
    employees,
    project_skills,
    project_team_members,
    projects,
    scope_items,
    skills,
    task_skill_requirements,
    wbs_nodes,
    wbs_nodes_with_code,
)

ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)
job_t = ai_jobs


class ActiveJobExistsError(Exception):
    """The one-active-job-per-(project, kind) unique index rejected the insert."""


@dataclass(frozen=True, slots=True)
class ActiveJob:
    id: uuid.UUID
    stale: bool


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    project_id: uuid.UUID
    kind: AiJobKind


def _job_select() -> sa.Select[Any]:
    return sa.select(
        job_t.c.id,
        job_t.c.project_id,
        job_t.c.kind,
        job_t.c.status,
        job_t.c.model,
        job_t.c.error,
        job_t.c.result,
        job_t.c.created_at,
        job_t.c.started_at,
        job_t.c.finished_at,
    )


def _older_than(seconds: int) -> sa.ColumnElement[bool]:
    cutoff = sa.func.now() - sa.func.make_interval(0, 0, 0, 0, 0, 0, seconds)
    return sa.func.coalesce(job_t.c.started_at, job_t.c.created_at) < cutoff


async def _has_wbs(conn: AsyncConnection, project_id: uuid.UUID) -> bool:
    stmt = sa.select(wbs_nodes.c.id).where(wbs_nodes.c.project_id == project_id).limit(1)
    return (await conn.execute(stmt)).first() is not None


class AiJobRepository:
    """User-facing queries: everything runs under the caller's RLS session."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        return await access.visible_project(self._conn, project_id) is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return await access.can_write_project(self._conn, project_id)

    async def has_wbs(self, project_id: uuid.UUID) -> bool:
        return await _has_wbs(self._conn, project_id)

    async def active_job(
        self, project_id: uuid.UUID, kind: AiJobKind, stale_after_s: int
    ) -> ActiveJob | None:
        stmt = (
            sa.select(job_t.c.id, _older_than(stale_after_s).label("stale"))
            .where(
                job_t.c.project_id == project_id,
                job_t.c.kind == kind,
                job_t.c.status.in_(ACTIVE),
            )
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return ActiveJob(id=row.id, stale=bool(row.stale)) if row else None

    async def create(self, project_id: uuid.UUID, kind: AiJobKind) -> uuid.UUID:
        job_id = uuid.uuid4()
        try:
            await self._conn.execute(
                sa.insert(job_t).values(
                    id=job_id, project_id=project_id, kind=kind, status=JobStatus.QUEUED
                )
            )
        except IntegrityError as exc:
            if pg_error_info(exc).sqlstate == "23505":
                raise ActiveJobExistsError from exc
            raise
        return job_id

    async def get(self, project_id: uuid.UUID, job_id: uuid.UUID) -> AiJob | None:
        stmt = _job_select().where(job_t.c.project_id == project_id, job_t.c.id == job_id)
        row = (await self._conn.execute(stmt)).first()
        return AiJob.model_validate(dict(row._mapping)) if row else None

    async def latest(self, project_id: uuid.UUID, kind: AiJobKind) -> AiJob | None:
        stmt = (
            _job_select()
            .where(job_t.c.project_id == project_id, job_t.c.kind == kind)
            .order_by(job_t.c.created_at.desc(), job_t.c.id.desc())
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return AiJob.model_validate(dict(row._mapping)) if row else None


# --------------------------------------------------------------------------- worker side
class AiJobStore:
    """Reads and writes made by the background worker on a service connection (bypasses RLS).

    Every query is scoped to the job's project explicitly, since RLS doesn't apply.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    # ----------------------------------------------------------------- job rows
    async def claim(self, job_id: uuid.UUID) -> ClaimedJob | None:
        stmt = (
            sa.update(job_t)
            .where(job_t.c.id == job_id, job_t.c.status == JobStatus.QUEUED)
            .values(status=JobStatus.RUNNING, started_at=sa.func.now())
            .returning(job_t.c.project_id, job_t.c.kind)
        )
        row = (await self._conn.execute(stmt)).first()
        return ClaimedJob(row.project_id, AiJobKind(row.kind)) if row else None

    async def finish(
        self,
        job_id: uuid.UUID,
        result: dict[str, Any],
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        await self._conn.execute(
            sa.update(job_t)
            .where(job_t.c.id == job_id)
            .values(
                status=JobStatus.SUCCEEDED,
                result=result,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finished_at=sa.func.now(),
            )
        )

    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        await self._conn.execute(
            sa.update(job_t)
            .where(job_t.c.id == job_id, job_t.c.status.in_(ACTIVE))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        result = await self._conn.execute(
            sa.update(job_t)
            .where(job_t.c.status.in_(ACTIVE), _older_than(older_than_s))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )
        return int(result.rowcount)

    # ----------------------------------------------------------------- inputs
    async def _scope(self, project_id: uuid.UUID) -> tuple[ScopeEntry, ...]:
        s = scope_items
        stmt = (
            sa.select(s.c.id, s.c.category, s.c.text, s.c.status)
            .where(s.c.project_id == project_id)
            .order_by(s.c.category, s.c.sort_order, s.c.created_at)
        )
        rows = (await self._conn.execute(stmt)).all()
        return tuple(ScopeEntry(r.id, r.category, r.text, r.status) for r in rows)

    async def team(self, project_id: uuid.UUID, org_id: uuid.UUID) -> tuple[list[TeamMember], bool]:
        """The project's team, or every active org employee when it has none.

        Returns the members (with skill name -> proficiency) and whether a team was set.
        """
        e, t = employees, project_team_members
        team_stmt = (
            sa.select(e.c.id, e.c.full_name, sa.func.coalesce(t.c.role_title, e.c.role_title))
            .select_from(t.join(e, e.c.id == t.c.employee_id))
            .where(t.c.project_id == project_id, e.c.active.is_(True))
            .order_by(e.c.full_name)
        )
        rows = (await self._conn.execute(team_stmt)).all()
        has_team = bool(rows)
        if not has_team:
            pool_stmt = (
                sa.select(e.c.id, e.c.full_name, e.c.role_title)
                .where(e.c.org_id == org_id, e.c.active.is_(True))
                .order_by(e.c.full_name)
            )
            rows = (await self._conn.execute(pool_stmt)).all()
        ids = [r[0] for r in rows]
        held: dict[uuid.UUID, dict[str, int]] = {i: {} for i in ids}
        if ids:
            es, sk = employee_skills, skills
            skill_stmt = (
                sa.select(es.c.employee_id, sk.c.name, es.c.proficiency)
                .select_from(es.join(sk, sk.c.id == es.c.skill_id))
                .where(es.c.employee_id.in_(ids))
            )
            for r in (await self._conn.execute(skill_stmt)).all():
                held[r.employee_id][str(r.name)] = int(r.proficiency)
        members = [TeamMember(r[0], r[1], r[2], held[r[0]]) for r in rows]
        return members, has_team

    async def generation_input(self, project_id: uuid.UUID) -> GenerationInput | None:
        p = projects
        row = (
            await self._conn.execute(
                sa.select(
                    p.c.org_id,
                    p.c.name,
                    p.c.description,
                    p.c.type,
                    p.c.methodology,
                    p.c.start_date,
                    p.c.target_end_date,
                ).where(p.c.id == project_id)
            )
        ).first()
        if row is None:
            return None
        org_skills: Sequence[Any] = (
            (
                await self._conn.execute(
                    sa.select(skills.c.name)
                    .where(skills.c.org_id == row.org_id)
                    .order_by(skills.c.name)
                )
            )
            .scalars()
            .all()
        )
        team, _ = await self.team(project_id, row.org_id)
        return GenerationInput(
            project_name=row.name,
            description=row.description,
            project_type=str(row.type),
            methodology=str(row.methodology),
            start_date=row.start_date,
            target_end_date=row.target_end_date,
            scope=await self._scope(project_id),
            org_skills=tuple(str(s) for s in org_skills),
            team=tuple(team),
        )

    async def validation_input(self, project_id: uuid.UUID) -> ValidationInput | None:
        p, c = projects, constraint_sets
        row = (
            await self._conn.execute(
                sa.select(
                    p.c.org_id,
                    p.c.name,
                    sa.func.coalesce(c.c.start_date, p.c.start_date).label("start_date"),
                    sa.func.coalesce(c.c.must_finish_by, p.c.target_end_date).label("deadline"),
                    sa.func.coalesce(c.c.working_days_per_week, 5).label("per_week"),
                )
                .select_from(p.outerjoin(c, c.c.project_id == p.c.id))
                .where(p.c.id == project_id)
            )
        ).first()
        if row is None:
            return None
        w, e = wbs_nodes_with_code, employees
        node_rows = (
            await self._conn.execute(
                sa.select(
                    w.c.id,
                    w.c.parent_id,
                    w.c.level,
                    w.c.code,
                    w.c.name,
                    w.c.duration_days,
                    w.c.owner_employee_id,
                    e.c.full_name.label("owner_name"),
                )
                .select_from(w.outerjoin(e, e.c.id == w.c.owner_employee_id))
                .where(w.c.project_id == project_id)
                .order_by(w.c.path)
            )
        ).all()
        nodes = tuple(NodeInfo(**r._mapping) for r in node_rows)
        d = dependencies
        dep_rows = (
            await self._conn.execute(
                sa.select(d.c.predecessor_id, d.c.successor_id, d.c.type, d.c.lag_days).where(
                    d.c.project_id == project_id
                )
            )
        ).all()
        deps = tuple(
            DependencyInfo(r.predecessor_id, r.successor_id, r.type, r.lag_days) for r in dep_rows
        )
        tsr, ps, sk, n = task_skill_requirements, project_skills, skills, wbs_nodes
        task_skills = (
            sa.select(sk.c.name)
            .select_from(
                tsr.join(sk, sk.c.id == tsr.c.skill_id).join(n, n.c.id == tsr.c.wbs_node_id)
            )
            .where(n.c.project_id == project_id)
        )
        project_required = (
            sa.select(sk.c.name)
            .select_from(ps.join(sk, sk.c.id == ps.c.skill_id))
            .where(ps.c.project_id == project_id, ps.c.required.is_(True))
        )
        required: Sequence[Any] = (
            (await self._conn.execute(sa.union(task_skills, project_required).order_by("name")))
            .scalars()
            .all()
        )
        team, has_team = await self.team(project_id, row.org_id)
        held = frozenset(
            " ".join(name.split()).casefold()
            for m in team
            for name, level in m.skills.items()
            if level
        )
        return ValidationInput(
            project_name=row.name,
            nodes=nodes,
            dependencies=deps,
            start_date=row.start_date,
            deadline=row.deadline,
            working_days_per_week=int(row.per_week),
            required_skills=tuple(str(s) for s in required),
            held_skills=held,
            has_team=has_team,
            scope=await self._scope(project_id),
        )

    # ----------------------------------------------------------------- WBS write
    async def has_wbs(self, project_id: uuid.UUID) -> bool:
        return await _has_wbs(self._conn, project_id)

    async def _skill_ids(self, org_id: uuid.UUID, names: list[str]) -> dict[str, uuid.UUID]:
        """Org skill ids by casefolded name, creating the missing skills (citext unique)."""

        async def existing() -> dict[str, uuid.UUID]:
            stmt = sa.select(skills.c.id, skills.c.name).where(skills.c.org_id == org_id)
            return {str(r.name).casefold(): r.id for r in (await self._conn.execute(stmt)).all()}

        known = await existing()
        missing = [n for n in names if n.casefold() not in known]
        if not missing:
            return known
        await self._conn.execute(
            pg_insert(skills)
            .values([{"id": uuid.uuid4(), "org_id": org_id, "name": n} for n in missing])
            .on_conflict_do_nothing()
        )
        return await existing()

    async def write_plan(
        self,
        project_id: uuid.UUID,
        plan: WbsPlan,
        owners: dict[str, uuid.UUID],
    ) -> dict[str, int] | None:
        """Write the root, deliverables, work packages, skills and dependencies.

        Returns the counts, or None when the project already has a WBS (or is gone).
        Work packages keep `estimate_confirmed_at` null: the PM confirms AI estimates.
        """
        project = (
            await self._conn.execute(
                sa.select(projects.c.org_id, projects.c.name).where(projects.c.id == project_id)
            )
        ).first()
        if project is None or await self.has_wbs(project_id):
            return None

        def node(
            parent: uuid.UUID | None, level: int, order: int, name: str, **extra: Any
        ) -> dict[str, Any]:
            return {
                "id": uuid.uuid4(),
                "project_id": project_id,
                "parent_id": parent,
                "level": level,
                "sort_order": order,
                "name": name,
                "description": extra.get("description"),
                "duration_days": extra.get("duration_days"),
                "owner_employee_id": extra.get("owner_employee_id"),
                "scope_item_id": extra.get("scope_item_id"),
                "origin": ItemOrigin.AI,
            }

        root = node(None, 1, 0, str(project.name)[:300])
        await self._conn.execute(sa.insert(wbs_nodes), [root])
        level2: list[dict[str, Any]] = []
        level3: list[dict[str, Any]] = []
        package_ids: dict[str, uuid.UUID] = {}
        package_skills: list[tuple[uuid.UUID, str]] = []
        for di, deliverable in enumerate(plan.deliverables):
            d_row = node(
                root["id"],
                2,
                di,
                deliverable.name,
                description=deliverable.description,
                scope_item_id=deliverable.scope_item_id,
            )
            level2.append(d_row)
            for pi, package in enumerate(deliverable.packages):
                p_row = node(
                    d_row["id"],
                    3,
                    pi,
                    package.name,
                    description=package.description,
                    duration_days=package.duration_days,
                    owner_employee_id=owners.get(package.key),
                    scope_item_id=package.scope_item_id,
                )
                level3.append(p_row)
                package_ids[package.key] = p_row["id"]
                package_skills.extend((p_row["id"], s) for s in package.skills)
        if level2:
            await self._conn.execute(sa.insert(wbs_nodes), level2)
        if level3:
            await self._conn.execute(sa.insert(wbs_nodes), level3)

        if package_skills:
            names = list({s.casefold(): s for _, s in package_skills}.values())
            ids = await self._skill_ids(project.org_id, names)
            requirements = {
                (node_id, ids[s.casefold()]) for node_id, s in package_skills if s.casefold() in ids
            }
            await self._conn.execute(
                sa.insert(task_skill_requirements),
                [
                    {"wbs_node_id": n, "skill_id": s, "min_proficiency": MIN_PROFICIENCY}
                    for n, s in sorted(requirements, key=str)
                ],
            )
            skill_ids = sorted({s for _, s in requirements}, key=str)
            upsert = pg_insert(project_skills).values(
                [{"project_id": project_id, "skill_id": s, "required": True} for s in skill_ids]
            )
            await self._conn.execute(
                upsert.on_conflict_do_update(
                    index_elements=[project_skills.c.project_id, project_skills.c.skill_id],
                    set_={"required": True},
                )
            )

        added = 0
        for dep in plan.dependencies:
            pred, succ = package_ids.get(dep.predecessor), package_ids.get(dep.successor)
            if pred is None or succ is None:
                continue
            try:
                # A savepoint per link: the cycle trigger may reject one without
                # aborting the whole WBS.
                async with self._conn.begin_nested():
                    await self._conn.execute(
                        sa.insert(dependencies).values(
                            id=uuid.uuid4(),
                            project_id=project_id,
                            predecessor_id=pred,
                            successor_id=succ,
                            type=dep.type,
                            lag_days=dep.lag_days,
                            origin=ItemOrigin.AI,
                        )
                    )
            except DBAPIError:
                continue
            added += 1
        return {"deliverables": len(level2), "workPackages": len(level3), "dependencies": added}
