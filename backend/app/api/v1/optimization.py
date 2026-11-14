"""Optimization endpoints: the inputs sheet, queueing a run and reading runs.

`GET …/optimization/inputs` is the confirmation sheet ("Check the inputs the
solver will use"). `POST …/optimization/runs` freezes the inputs (with the
body's overrides, remedy and baseline applied) into a queued run that the
in-process worker (`app/jobs/optimizer.py`) solves after the response. The
run's `options` are the body as sent plus `inputsHash`: the hash the inputs
sheet showed for the saved inputs, so the screen can tell when the latest
plan is out of date. With `changeRequestId` this is also the re-plan of a
change on step 7.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from app.api.deps import OptimizationRuns, Optimizer, ProjectId, RunId
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.domain.optimization import InputRow, OptimizationInputs, OptimizationRun, RunCreate
from app.jobs.optimizer import RESTART_ERROR
from app.optimization.input_sheet import Blocker, Sheet, blocking_detail, build_sheet
from app.optimization.inputs import inputs_hash
from app.optimization.model import PlanningInput
from app.optimization.overrides import apply_overrides, with_applied_dates, with_baseline
from app.optimization.remedies import apply_remedy
from app.repositories.optimization import ActiveRunExistsError, ProjectContext

router = APIRouter(tags=["optimization"])

ACTIVE_RUN_DETAIL = "An optimization is already running for this project."
DECIDED_CHANGE_DETAIL = "This change was already decided; record a new change to re-plan."
DECIDED_CHANGE_STATUSES = ("approved", "rejected")
# A remedy the PM picked answers the question it relaxes, for that run only.
LIFTED_BY_REMEDY = {"specialist": "specialist", "move_deadline": "deadline"}


async def _project(runs: OptimizationRuns, project_id: uuid.UUID) -> ProjectContext:
    project = await runs.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def _applied_dates(
    runs: OptimizationRuns, project_id: uuid.UUID, run_id: uuid.UUID
) -> dict[str, tuple[date, date]]:
    """WBS node id -> first and last day of each work package in an earlier run's plan."""
    run = await runs.get(project_id, run_id)
    if run is None or run.result is None:
        return {}
    return {item.wbs_node_id: (item.start, item.end) for item in run.result.schedule}


async def _sheet(runs: OptimizationRuns, project: ProjectContext) -> tuple[PlanningInput, Sheet]:
    inp = await runs.load_input(project, datetime.now(UTC).date())
    return inp, build_sheet(await runs.input_facts(project, inp))


@router.get(
    "/projects/{projectId}/optimization/inputs",
    response_model=OptimizationInputs,
    summary="Every input the solver will use, where it came from, and what blocks a run",
)
async def get_inputs(project_id: ProjectId, runs: OptimizationRuns) -> OptimizationInputs:
    """The confirmation sheet shown before a run (`ready` false while `blocking` is non-empty)."""
    project = await _project(runs, project_id)
    inp, sheet = await _sheet(runs, project)
    return OptimizationInputs(
        rows=[
            InputRow(
                key=row.key, label=row.label, value=row.value, source=row.source, from_=row.from_
            )
            for row in sheet.rows
        ],
        ready=sheet.ready,
        blocking=sheet.blocking,
        missing=sheet.missing,
        hash=inputs_hash(inp),
    )


@router.post(
    "/projects/{projectId}/optimization/runs",
    response_model=OptimizationRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an optimization run (solved in the background)",
)
async def create_run(
    project_id: ProjectId,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    runs: OptimizationRuns,
    optimizer: Optimizer,
    body: RunCreate | None = None,
) -> OptimizationRun:
    """Freeze the inputs into a queued run; 422 with `missing` while the sheet isn't ready."""
    body = body or RunCreate()
    project = await _project(runs, project_id)
    if not await runs.can_write(project_id):
        raise ForbiddenError("You do not have permission to optimize this project")

    stale_after_s = request.app.state.settings.optimizer_stale_after_s
    active = await runs.active_run(project_id, stale_after_s)
    if active is not None:
        if not active.stale:
            raise ConflictError(ACTIVE_RUN_DETAIL)
        await runs.fail_run(active.id, RESTART_ERROR)  # orphaned by a restart

    inp, sheet = await _sheet(runs, project)
    # Fingerprint of the saved inputs, before this run's baseline, overrides and
    # remedy: exactly the `hash` GET …/optimization/inputs returns for the same data.
    saved_inputs_hash = inputs_hash(inp)
    lifted = LIFTED_BY_REMEDY.get(body.remedy or "")
    blockers: list[Blocker] = [blocker for blocker in sheet.blockers if blocker.key != lifted]
    if blockers:
        raise UnprocessableError(
            blocking_detail([blocker.message for blocker in blockers]),
            extra={"missing": [blocker.key for blocker in blockers]},
        )

    if body.change_request_id is not None:
        change_status = await runs.change_request_status(project_id, body.change_request_id)
        if change_status is None:
            raise NotFoundError("Change request not found")
        if change_status in DECIDED_CHANGE_STATUSES:
            raise ConflictError(DECIDED_CHANGE_DETAIL)
    if body.baseline_run_id is not None:
        owners = await runs.baseline_owners(project_id, body.baseline_run_id)
        if owners is None:
            raise NotFoundError("Baseline run not found")
        inp = with_baseline(inp, owners)
        inp = with_applied_dates(inp, await _applied_dates(runs, project_id, body.baseline_run_id))

    # The body exactly as sent (camelCase, ids and dates as strings): the run's setup.
    options = body.model_dump(mode="json", by_alias=True, exclude_none=True)
    inp = apply_remedy(apply_overrides(inp, options.get("overrides")), body.remedy)
    options["inputsHash"] = saved_inputs_hash

    try:
        run_id = await runs.create(project_id, inp.to_snapshot(), options)
    except ActiveRunExistsError as exc:  # lost a race with a concurrent request
        raise ConflictError(ACTIVE_RUN_DETAIL) from exc
    if body.change_request_id is not None:
        await runs.mark_replanning(project_id, body.change_request_id, run_id)
    run = await runs.get(project_id, run_id)
    if run is None:
        raise ForbiddenError("The run was created but is not visible to you")

    # Background tasks run after the response, i.e. after the transaction has
    # committed, so the worker always finds the queued row.
    background.add_task(optimizer.submit, run_id)
    response.headers["Location"] = f"/api/v1/projects/{project_id}/optimization/runs/{run_id}"
    return run


# Declared before /{runId} so "latest" isn't parsed as a run id.
@router.get(
    "/projects/{projectId}/optimization/runs/latest",
    response_model=OptimizationRun,
    summary="Latest optimization run (404 when the project has none)",
)
async def latest_run(project_id: ProjectId, runs: OptimizationRuns) -> OptimizationRun:
    """The project's newest run, whatever its status."""
    run = await runs.latest(project_id)
    if run is None:
        raise NotFoundError("No optimization runs yet")
    return run


@router.get(
    "/projects/{projectId}/optimization/runs/{runId}",
    response_model=OptimizationRun,
    summary="Get an optimization run",
)
async def get_run(project_id: ProjectId, run_id: RunId, runs: OptimizationRuns) -> OptimizationRun:
    """One run of the project (404 when it isn't this project's or isn't visible)."""
    run = await runs.get(project_id, run_id)
    if run is None:
        raise NotFoundError("Optimization run not found")
    return run
