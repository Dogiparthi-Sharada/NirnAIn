"""Applying a plan, plan versions and plan exports."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import OptimizationRuns, Plans, ProjectId, RunId
from app.core.errors import NotFoundError, UnprocessableError
from app.domain.common import SolverStatus
from app.domain.optimization import OptimizationRun
from app.domain.plan import PlanVersion
from app.export.model import build_export
from app.export.msproject import render_msproject
from app.export.pdf import render_pdf

router = APIRouter(tags=["plan"])

SUCCESSFUL = (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
RUN_NOT_FOUND = "Optimization run not found"


class ExportFormat(StrEnum):
    PDF = "pdf"
    MSPROJECT = "msproject"


@router.post(
    "/projects/{projectId}/optimization/runs/{runId}/apply",
    response_model=OptimizationRun,
    summary="Apply a successful run: it becomes the project's next plan version",
    responses={403: {"description": "Not a writer"}, 422: {"description": "Not appliable"}},
)
async def apply_run(
    project_id: ProjectId, run_id: RunId, runs: OptimizationRuns, plans: Plans
) -> OptimizationRun:
    if await runs.get(project_id, run_id) is None:
        raise NotFoundError(RUN_NOT_FOUND)
    await plans.apply(run_id)
    run = await runs.get(project_id, run_id)
    if run is None:
        raise NotFoundError(RUN_NOT_FOUND)
    return run


@router.get(
    "/projects/{projectId}/plan-versions",
    response_model=list[PlanVersion],
    summary="Applied plan versions, oldest first",
)
async def plan_versions(project_id: ProjectId, plans: Plans) -> list[PlanVersion]:
    if not await plans.project_visible(project_id):
        raise NotFoundError("Project not found")
    return await plans.versions(project_id)


@router.get(
    "/projects/{projectId}/optimization/runs/{runId}/export",
    response_class=Response,
    summary="Download a plan as PDF or Microsoft Project XML",
    responses={
        200: {"content": {"application/pdf": {}, "application/xml": {}}},
        422: {"description": "The run has no plan"},
    },
)
async def export_run(
    project_id: ProjectId,
    run_id: RunId,
    plans: Plans,
    export_format: Annotated[ExportFormat, Query(alias="format")] = ExportFormat.PDF,
) -> Response:
    header = await plans.export_header(project_id, run_id)
    if header is None:
        raise NotFoundError(RUN_NOT_FOUND)
    if header.status not in SUCCESSFUL:
        raise UnprocessableError("Only a successful run can be exported")
    plan = build_export(header, await plans.export_rows(run_id))
    if export_format is ExportFormat.PDF:
        content = await asyncio.to_thread(render_pdf, plan)
        media_type, ext = "application/pdf", "pdf"
    else:
        content = render_msproject(plan)
        media_type, ext = "application/xml", "xml"
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{plan.file_stem}.{ext}"'},
    )
