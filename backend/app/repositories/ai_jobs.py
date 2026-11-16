"""AI jobs (WBS generate / validate): queueing and reading them, and the worker's job rows.

Purpose: Allow one active job per project and kind, show its progress and result, and give the
worker the project data a generation or review reads (scope, team, skills, WBS checks input).
How it fits: `AiJobRepository` runs under the caller's RLS session for app/api/v1/ai.py;
`AiJobStore` is the worker's service connection in app/jobs/ai_jobs.py and writes generated WBSs
through `GeneratedWbsWriter` (app/repositories/ai_wbs_writer.py); layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.wbs_generate import (
    GenerationInput,
    ScopeEntry,
    TeamMember,
)
from app.ai.wbs_validate import ValidationInput
from app.core.errors import pg_error_info
from app.domain.ai import AiJob
from app.domain.common import AiJobKind, JobStatus
from app.repositories import access
from app.repositories.ai_wbs_writer import GeneratedWbsWriter, project_has_wbs
from app.repositories.job_rows import started_before
from app.repositories.tables import (
    ai_jobs,
    employee_skills,
    employees,
    project_team_members,
    projects,
    scope_items,
    skills,
)
from app.repositories.wbs_checks import read_validation_input

ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)


class ActiveJobExistsError(Exception):
    """The one-active-job-per-(project, kind) unique index rejected the insert."""


@dataclass(frozen=True, slots=True)
class ActiveJob:
    """A queued or running AI job and whether it has been active for too long (orphaned)."""

    id: uuid.UUID
    stale: bool


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """A job the worker has started: its project, kind and who asked for it."""

    project_id: uuid.UUID
    kind: AiJobKind
    requested_by: uuid.UUID | None = None


def _job_select() -> sa.Select[Any]:
    """Return the select of the AI job columns the API shows."""
    return sa.select(
        ai_jobs.c.id,
        ai_jobs.c.project_id,
        ai_jobs.c.kind,
        ai_jobs.c.status,
        ai_jobs.c.model,
        ai_jobs.c.error,
        ai_jobs.c.result,
        ai_jobs.c.created_at,
        ai_jobs.c.started_at,
        ai_jobs.c.finished_at,
    )


class AiJobRepository:
    """User-facing queries: everything runs under the caller's RLS session."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        """Tell whether the caller can see the project."""
        return await access.visible_project(self._conn, project_id) is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        """Tell whether the caller may change the project."""
        return await access.can_write_project(self._conn, project_id)

    async def has_wbs(self, project_id: uuid.UUID) -> bool:
        """Tell whether the project already has a WBS."""
        return await project_has_wbs(self._conn, project_id)

    async def active_job(
        self, project_id: uuid.UUID, kind: AiJobKind, stale_after_s: int
    ) -> ActiveJob | None:
        """Return the active job of this kind, flagged stale after `stale_after_s`, or None."""
        stmt = (
            sa.select(ai_jobs.c.id, started_before(ai_jobs, stale_after_s).label("stale"))
            .where(
                ai_jobs.c.project_id == project_id,
                ai_jobs.c.kind == kind,
                ai_jobs.c.status.in_(ACTIVE),
            )
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return ActiveJob(id=row.id, stale=bool(row.stale)) if row else None

    async def create(self, project_id: uuid.UUID, kind: AiJobKind) -> uuid.UUID:
        """Queue a job and return its id.

        Raises `ActiveJobExistsError` when the one-active-job unique index rejects it.
        """
        job_id = uuid.uuid4()
        try:
            await self._conn.execute(
                sa.insert(ai_jobs).values(
                    id=job_id, project_id=project_id, kind=kind, status=JobStatus.QUEUED
                )
            )
        except IntegrityError as exc:
            if pg_error_info(exc).sqlstate == "23505":
                raise ActiveJobExistsError from exc
            raise
        return job_id

    async def get(self, project_id: uuid.UUID, job_id: uuid.UUID) -> AiJob | None:
        """Return one of the project's AI jobs, or None."""
        stmt = _job_select().where(ai_jobs.c.project_id == project_id, ai_jobs.c.id == job_id)
        row = (await self._conn.execute(stmt)).first()
        return AiJob.model_validate(dict(row._mapping)) if row else None

    async def latest(self, project_id: uuid.UUID, kind: AiJobKind) -> AiJob | None:
        """Return the project's newest job of this kind, or None."""
        stmt = (
            _job_select()
            .where(ai_jobs.c.project_id == project_id, ai_jobs.c.kind == kind)
            .order_by(ai_jobs.c.created_at.desc(), ai_jobs.c.id.desc())
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        return AiJob.model_validate(dict(row._mapping)) if row else None


# --------------------------------------------------------------------------- worker side
class AiJobStore(GeneratedWbsWriter):
    """Reads and writes made by the background worker on a service connection (bypasses RLS).

    Every query is scoped to the job's project explicitly, since RLS doesn't apply.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the worker's service connection."""
        self._conn = conn

    # ----------------------------------------------------------------- job rows
    async def claim(self, job_id: uuid.UUID) -> ClaimedJob | None:
        """Move a queued job to running; returns its project, kind and requester.

        None when the job is not queued.
        """
        stmt = (
            sa.update(ai_jobs)
            .where(ai_jobs.c.id == job_id, ai_jobs.c.status == JobStatus.QUEUED)
            .values(status=JobStatus.RUNNING, started_at=sa.func.now())
            .returning(ai_jobs.c.project_id, ai_jobs.c.kind, ai_jobs.c.requested_by)
        )
        row = (await self._conn.execute(stmt)).first()
        return ClaimedJob(row.project_id, AiJobKind(row.kind), row.requested_by) if row else None

    async def finish(
        self,
        job_id: uuid.UUID,
        result: dict[str, Any],
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Store a job's result, the model that made it and its token use, and mark it succeeded."""
        await self._conn.execute(
            sa.update(ai_jobs)
            .where(ai_jobs.c.id == job_id)
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
        """Mark an active job failed with the error shown to the user."""
        await self._conn.execute(
            sa.update(ai_jobs)
            .where(ai_jobs.c.id == job_id, ai_jobs.c.status.in_(ACTIVE))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        """Fail jobs active for over `older_than_s` (orphaned by a restart); return the count."""
        result = await self._conn.execute(
            sa.update(ai_jobs)
            .where(ai_jobs.c.status.in_(ACTIVE), started_before(ai_jobs, older_than_s))
            .values(status=JobStatus.FAILED, error=error, finished_at=sa.func.now())
        )
        return int(result.rowcount)

    # ----------------------------------------------------------------- inputs
    async def _scope(self, project_id: uuid.UUID) -> tuple[ScopeEntry, ...]:
        """Return the project's scope items by category and order."""
        stmt = (
            sa.select(
                scope_items.c.id, scope_items.c.category, scope_items.c.text, scope_items.c.status
            )
            .where(scope_items.c.project_id == project_id)
            .order_by(scope_items.c.category, scope_items.c.sort_order, scope_items.c.created_at)
        )
        rows = (await self._conn.execute(stmt)).all()
        return tuple(ScopeEntry(row.id, row.category, row.text, row.status) for row in rows)

    async def team(self, project_id: uuid.UUID, org_id: uuid.UUID) -> tuple[list[TeamMember], bool]:
        """The project's team, or every active org employee when it has none.

        Returns the members (with skill name -> proficiency) and whether a team was set.
        """
        team_stmt = (
            sa.select(
                employees.c.id,
                employees.c.full_name,
                sa.func.coalesce(project_team_members.c.role_title, employees.c.role_title),
            )
            .select_from(
                project_team_members.join(
                    employees, employees.c.id == project_team_members.c.employee_id
                )
            )
            .where(project_team_members.c.project_id == project_id, employees.c.active.is_(True))
            .order_by(employees.c.full_name)
        )
        rows = (await self._conn.execute(team_stmt)).all()
        has_team = bool(rows)
        if not has_team:
            pool_stmt = (
                sa.select(employees.c.id, employees.c.full_name, employees.c.role_title)
                .where(employees.c.org_id == org_id, employees.c.active.is_(True))
                .order_by(employees.c.full_name)
            )
            rows = (await self._conn.execute(pool_stmt)).all()
        ids = [row[0] for row in rows]
        held: dict[uuid.UUID, dict[str, int]] = {employee_id: {} for employee_id in ids}
        if ids:
            skill_stmt = (
                sa.select(
                    employee_skills.c.employee_id, skills.c.name, employee_skills.c.proficiency
                )
                .select_from(
                    employee_skills.join(skills, skills.c.id == employee_skills.c.skill_id)
                )
                .where(employee_skills.c.employee_id.in_(ids))
            )
            for row in (await self._conn.execute(skill_stmt)).all():
                held[row.employee_id][str(row.name)] = int(row.proficiency)
        members = [TeamMember(row[0], row[1], row[2], held[row[0]]) for row in rows]
        return members, has_team

    async def generation_input(self, project_id: uuid.UUID) -> GenerationInput | None:
        """Return what WBS generation reads: the project, its scope, the org's skills and the team.

        Returns None when the project is gone.
        """
        row = (
            await self._conn.execute(
                sa.select(
                    projects.c.org_id,
                    projects.c.name,
                    projects.c.description,
                    projects.c.type,
                    projects.c.methodology,
                    projects.c.start_date,
                    projects.c.target_end_date,
                ).where(projects.c.id == project_id)
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
            org_skills=tuple(str(skill_name) for skill_name in org_skills),
            team=tuple(team),
        )

    async def validation_input(
        self, project_id: uuid.UUID, today: date | None = None
    ) -> ValidationInput | None:
        """The checks' input (`wbs_checks.py`, the same as `GET …/wbs/checks`) plus the
        scope items the review reads; `today` defaults to the UTC date, like the optimizer."""
        checks_input = await read_validation_input(
            self._conn, project_id, today or datetime.now(UTC).date()
        )
        if checks_input is None:
            return None
        return replace(checks_input, scope=await self._scope(project_id))
