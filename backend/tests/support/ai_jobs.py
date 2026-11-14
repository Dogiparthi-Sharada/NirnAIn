"""Moved test support."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.domain.ai import AiJob
from app.domain.common import AiJobKind, JobStatus, OrgRole
from app.repositories.ai_jobs import ActiveJob, ActiveJobExistsError
from tests.support.clock import NOW
from tests.support.store import Store


# --------------------------------------------------------------------------- API
@dataclass
class JobData:
    jobs: dict[uuid.UUID, AiJob] = field(default_factory=dict)
    has_wbs: bool = False
    active: ActiveJob | None = None
    race: bool = False


class FakeJobs:
    def __init__(self, store: Store, data: JobData, user_id: uuid.UUID) -> None:
        self.s, self.d, self.uid = store, data, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def has_wbs(self, project_id: uuid.UUID) -> bool:
        return self.d.has_wbs

    async def active_job(
        self, project_id: uuid.UUID, kind: AiJobKind, stale_after_s: int
    ) -> ActiveJob | None:
        return self.d.active

    async def create(self, project_id: uuid.UUID, kind: AiJobKind) -> uuid.UUID:
        if self.d.race:
            raise ActiveJobExistsError
        job = AiJob(
            id=uuid.uuid4(),
            project_id=project_id,
            kind=kind,
            status=JobStatus.QUEUED,
            error=None,
            result=None,
            created_at=NOW,
            started_at=None,
            finished_at=None,
        )
        self.d.jobs[job.id] = job
        return job.id

    async def get(self, project_id: uuid.UUID, job_id: uuid.UUID) -> AiJob | None:
        return self.d.jobs.get(job_id)

    async def latest(self, project_id: uuid.UUID, kind: AiJobKind) -> AiJob | None:
        rows = [
            j
            for j in self.d.jobs.values()
            if j.project_id == project_id and j.kind == kind and self._role(project_id)
        ]
        return rows[-1] if rows else None


class FakeRunner:
    def __init__(self) -> None:
        self.submitted: list[uuid.UUID] = []
        self.failed: list[tuple[uuid.UUID, str]] = []

    async def submit(self, job_id: uuid.UUID) -> None:
        self.submitted.append(job_id)

    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        self.failed.append((job_id, error))
