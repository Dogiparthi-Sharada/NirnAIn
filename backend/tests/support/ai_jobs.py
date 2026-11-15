"""Fake AI job repository for the AI job endpoint tests.

Purpose: Let the endpoint tests set what the database would say (a WBS exists, a job is active, a
concurrent insert wins) and see the jobs a request queued.
How it fits: The `jobs_client` fixture (tests/ai_jobs/conftest.py) installs it in place of
`AiJobRepository`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.domain.ai import AiJob
from app.domain.common import AiJobKind, JobStatus
from app.repositories.ai_jobs import ActiveJob, ActiveJobExistsError
from tests.support.clock import NOW
from tests.support.store import ProjectScopedFake, Store


# --------------------------------------------------------------------------- API
@dataclass
class JobData:
    """What the fake AI job table holds and the database states a test can switch on."""

    jobs: dict[uuid.UUID, AiJob] = field(default_factory=dict)
    has_wbs: bool = False
    active: ActiveJob | None = None
    race: bool = False


class FakeJobs(ProjectScopedFake):
    """Stand-in for `AiJobRepository` over the job data."""

    def __init__(self, store: Store, data: JobData, user_id: uuid.UUID) -> None:
        """Act as the given user over the store and the job data."""
        super().__init__(store, user_id)
        self.data = data

    async def has_wbs(self, project_id: uuid.UUID) -> bool:
        """Return the WBS state the test set."""
        return self.data.has_wbs

    async def active_job(
        self, project_id: uuid.UUID, kind: AiJobKind, stale_after_s: int
    ) -> ActiveJob | None:
        """Return the active job the test set, if any."""
        return self.data.active

    async def create(self, project_id: uuid.UUID, kind: AiJobKind) -> uuid.UUID:
        """Queue a job, or raise `ActiveJobExistsError` when the test simulates a lost race."""
        if self.data.race:
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
        self.data.jobs[job.id] = job
        return job.id

    async def get(self, project_id: uuid.UUID, job_id: uuid.UUID) -> AiJob | None:
        """Return a job by id."""
        return self.data.jobs.get(job_id)

    async def latest(self, project_id: uuid.UUID, kind: AiJobKind) -> AiJob | None:
        """Return the newest visible job of a kind for the project."""
        rows = [
            job
            for job in self.data.jobs.values()
            if job.project_id == project_id and job.kind == kind and self._role(project_id)
        ]
        return rows[-1] if rows else None
