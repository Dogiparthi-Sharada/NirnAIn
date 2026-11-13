from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, status

from app.api.deps import AiJobs, AiRunner, ProjectId
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.domain.ai import AiJob
from app.domain.common import AiJobKind
from app.jobs.ai_jobs import HAS_WBS_ERROR, RESTART_ERROR
from app.repositories.ai_jobs import ActiveJobExistsError

router = APIRouter(tags=["ai"])

ACTIVE_DETAIL = {
    AiJobKind.WBS_GENERATE: "WBS generation is already running for this project.",
    AiJobKind.WBS_VALIDATE: "WBS validation is already running for this project.",
}


async def _start(
    kind: AiJobKind,
    project_id: ProjectId,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    jobs: AiJobs,
    runner: AiRunner,
) -> AiJob:
    if not await jobs.project_exists(project_id):
        raise NotFoundError("Project not found")
    if not await jobs.can_write(project_id):
        raise ForbiddenError("You do not have permission to change this project's WBS")
    if kind == AiJobKind.WBS_GENERATE and await jobs.has_wbs(project_id):
        raise ConflictError(HAS_WBS_ERROR)

    stale_after_s = request.app.state.settings.ai_job_stale_after_s
    active = await jobs.active_job(project_id, kind, stale_after_s)
    if active is not None:
        if not active.stale:
            raise ConflictError(ACTIVE_DETAIL[kind])
        await runner.fail(active.id, RESTART_ERROR)  # orphaned by a restart

    try:
        job_id = await jobs.create(project_id, kind)
    except ActiveJobExistsError as exc:  # lost a race with a concurrent request
        raise ConflictError(ACTIVE_DETAIL[kind]) from exc
    job = await jobs.get(project_id, job_id)
    if job is None:
        raise ForbiddenError("The job was created but is not visible to you")

    # Runs after the response, i.e. after the transaction has committed.
    background.add_task(runner.submit, job_id)
    response.headers["Location"] = f"/api/v1/projects/{project_id}/ai-jobs/latest?kind={kind}"
    return job


@router.post(
    "/projects/{projectId}/wbs/generate",
    response_model=AiJob,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate the WBS from the accepted scope (in the background)",
)
async def generate_wbs(
    project_id: ProjectId,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    jobs: AiJobs,
    runner: AiRunner,
) -> AiJob:
    return await _start(
        AiJobKind.WBS_GENERATE, project_id, request, response, background, jobs, runner
    )


@router.post(
    "/projects/{projectId}/wbs/validate",
    response_model=AiJob,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Check the WBS for structural problems and missing work (in the background)",
)
async def validate_wbs(
    project_id: ProjectId,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    jobs: AiJobs,
    runner: AiRunner,
) -> AiJob:
    return await _start(
        AiJobKind.WBS_VALIDATE, project_id, request, response, background, jobs, runner
    )


@router.get(
    "/projects/{projectId}/ai-jobs/latest",
    response_model=AiJob,
    summary="Latest AI job of a kind (404 when there is none)",
)
async def latest_job(
    project_id: ProjectId, kind: Annotated[AiJobKind, Query()], jobs: AiJobs
) -> AiJob:
    job = await jobs.latest(project_id, kind)
    if job is None:
        raise NotFoundError("No AI jobs of this kind yet")
    return job
