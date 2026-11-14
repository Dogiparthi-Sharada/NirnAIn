"""Plan endpoints: applying a run, listing plan versions and exporting a plan.

Purpose: Round 3, R1: a run goes into force only while the project's saved inputs still hash to the
`inputsHash` it was solved from; otherwise applying it is refused with 409 and the PM runs the
optimizer again. Applied plans become versions that can be downloaded as PDF or MS Project XML.
How it fits: Uses app/repositories/plan.py and app/repositories/optimization.py; renders through
app/export; layer: api.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import OptimizationRuns, Plans, ProjectId, RunId
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.domain.common import SolverStatus
from app.domain.optimization import OptimizationRun
from app.domain.plan import PlanVersion
from app.export.model import build_export
from app.export.msproject import render_msproject
from app.export.pdf import render_pdf
from app.optimization.inputs import inputs_hash

router = APIRouter(tags=["plan"])

SUCCESSFUL = (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
RUN_NOT_FOUND = "Optimization run not found"
STALE_RUN_DETAIL = (
    "The inputs changed since this plan was computed. Run the optimizer again before applying."
)


class ExportFormat(StrEnum):
    """The file formats a plan can be downloaded in."""

    PDF = "pdf"
    MSPROJECT = "msproject"


@router.post(
    "/projects/{projectId}/optimization/runs/{runId}/apply",
    response_model=OptimizationRun,
    summary="Apply a successful run: it becomes the project's next plan version",
    responses={
        403: {"description": "Not a writer"},
        409: {"description": "The inputs changed since the run was solved"},
        422: {"description": "Not appliable"},
    },
)
async def apply_run(
    project_id: ProjectId, run_id: RunId, runs: OptimizationRuns, plans: Plans
) -> OptimizationRun:
    """Make a successful run the next plan version (409 when its inputs are outdated)."""
    candidate = await runs.get(project_id, run_id)
    if candidate is None:
        raise NotFoundError(RUN_NOT_FOUND)
    if not await runs.can_write(project_id):
        raise ForbiddenError("You do not have permission to apply this run")
    # Unsolved or already applied runs are refused by the apply function itself (422).
    if candidate.status in SUCCESSFUL and candidate.version is None:
        project = await runs.project(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        saved = await runs.load_input(project, datetime.now(UTC).date())
        # A run without a recorded hash can't prove it is current: it is outdated too.
        if candidate.options.get("inputsHash") != inputs_hash(saved):
            raise ConflictError(STALE_RUN_DETAIL)
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
    """Return the project's applied plan versions, oldest first; 404 when the project is hidden."""
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
    """Return a successful run's plan as a PDF or MS Project XML download.

    Returns 404 for an unknown run and 422 for a run without a plan.
    """
    header = await plans.export_header(project_id, run_id)
    if header is None:
        raise NotFoundError(RUN_NOT_FOUND)
    if header.status not in SUCCESSFUL:
        raise UnprocessableError("Only a successful run can be exported")
    plan = build_export(header, await plans.export_rows(run_id))
    if export_format is ExportFormat.PDF:
        content = await asyncio.to_thread(render_pdf, plan)
        media_type, extension = "application/pdf", "pdf"
    else:
        content = render_msproject(plan)
        media_type, extension = "application/xml", "xml"
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{plan.file_stem}.{extension}"'},
    )
