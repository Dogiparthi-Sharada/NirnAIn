"""Optimization endpoints: the inputs sheet, queueing a run and reading runs.

Purpose: `GET …/optimization/inputs` is the "Check the inputs the solver will use" sheet with the
inputs hash; `POST …/optimization/runs` freezes the inputs (with the body's overrides, remedy and
baseline) into a queued run. A run while a plan is in force starts from that plan unless the body
names another `baselineRunId`; with `reviewedHash` it is refused (409) when the inputs changed since
the PM's review; with `changeRequestId` it is the re-plan of a step-7 change. The run's `options`
are the body as sent plus `inputsHash` and `solverSettings`.
How it fits: Uses app/repositories/optimization.py and the pure app/optimization modules; the
in-process worker (app/jobs/optimizer.py) solves the run after the response; layer: api.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from app.api.deps import OptimizationRuns, Optimizer, ProjectId, RunId
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.domain.optimization import InputRow, OptimizationInputs, OptimizationRun, RunCreate
from app.jobs.optimizer import RESTART_ERROR, run_settings
from app.optimization.input_sheet import Blocker, Row, Sheet, blocking_detail, build_sheet
from app.optimization.inputs import inputs_hash
from app.optimization.model import PlanningInput
from app.optimization.overrides import apply_overrides, with_baseline, with_plan_in_force
from app.optimization.remedies import apply_remedy
from app.repositories.optimization import ActiveRunExistsError, ProjectContext

router = APIRouter(tags=["optimization"])

ACTIVE_RUN_DETAIL = "An optimization is already running for this project."
STALE_REVIEW_DETAIL = "The inputs changed since you reviewed them. Review again."
DECIDED_CHANGE_DETAIL = "This change was already decided; record a new change to re-plan."
DECIDED_CHANGE_STATUSES = ("approved", "rejected")
# A remedy the PM picked answers the questions it relaxes, for that run only.
LIFTED_BY_REMEDY = {
    "specialist": ("specialist",),
    "move_deadline": ("deadline",),
    "combined": ("specialist", "deadline"),
}
DURATIONS_KEY = "durations"
LISTED_PACKAGES = 5


class AppliedInputs(OptimizationInputs):
    """The confirmation sheet plus the inputs hash of the plan in force (round 2, D3):
    step 6 says "Inputs changed since Plan vN" when `hash` differs from `appliedHash`.
    Declared beside its endpoint so the optimizer's domain module stays as it is."""

    applied_hash: str | None = None


async def _project(runs: OptimizationRuns, project_id: uuid.UUID) -> ProjectContext:
    """Return the project; 404 when it is hidden."""
    project = await runs.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def _plan_in_force(
    runs: OptimizationRuns, project_id: uuid.UUID, run_id: uuid.UUID
) -> tuple[dict[str, tuple[date, date]], date | None]:
    """An earlier run's plan: each work package's first and last day, and its completion."""
    run = await runs.get(project_id, run_id)
    if run is None or run.result is None:
        return {}, None
    dates = {item.wbs_node_id: (item.start, item.end) for item in run.result.schedule}
    summary = run.result.summary
    return dates, summary.completion_date if summary is not None else None


def _with_unplanned_work(sheet: Sheet, packages: list[str]) -> Sheet:
    """The sheet with a blocker naming the work packages the solver can't plan.

    Business rule (WBS-003, WBS-004): a work package without a usable duration is never
    left out of a plan silently; the run waits until a person enters one on step 4.
    """
    if not packages:
        return sheet
    if len(packages) == 1:
        message = f"{packages[0]} has no duration: enter one on step 4"
    else:
        names = packages[:LISTED_PACKAGES]
        if len(packages) > LISTED_PACKAGES:
            names.append(f"{len(packages) - LISTED_PACKAGES} more")
        message = f"{', '.join(names[:-1])} and {names[-1]} have no duration: enter them on step 4"

    def flagged(row: Row) -> Row:
        """Mark the work packages row as needing the PM when some package has no duration."""
        if row.key != "workPackages":
            return row
        return replace(
            row, value=f"{row.value} · {len(packages)} without a duration", source="needs_you"
        )

    return Sheet(
        rows=[flagged(row) for row in sheet.rows],
        blockers=[Blocker(DURATIONS_KEY, message), *sheet.blockers],
    )


async def _sheet(runs: OptimizationRuns, project: ProjectContext) -> tuple[PlanningInput, Sheet]:
    """Load the saved inputs and build the confirmation sheet, unplanned work included."""
    planning_input = await runs.load_input(project, datetime.now(UTC).date())
    sheet = build_sheet(await runs.input_facts(project, planning_input))
    return planning_input, _with_unplanned_work(
        sheet, await runs.packages_without_duration(project.id)
    )


async def _from_baseline(
    runs: OptimizationRuns,
    project_id: uuid.UUID,
    planning_input: PlanningInput,
    baseline_run_id: uuid.UUID | None,
) -> PlanningInput:
    """The input with the baseline's owners as current owners (404 for an unknown run).

    Business rule (round 2, D3): without a `baselineRunId`, a run while a plan is in force
    starts from that plan, so work packages and owners move as little as possible. The
    run's stored body stays as sent.
    """
    if baseline_run_id is None:
        applied = await runs.applied_run(project_id)
        if applied is None:
            return planning_input
        baseline_run_id = applied.id
    owners = await runs.baseline_owners(project_id, baseline_run_id)
    if owners is None:
        raise NotFoundError("Baseline run not found")
    # D2: the baseline's dates are the plan in force, so its completion is the date to keep.
    with_owners = with_baseline(planning_input, owners)
    return with_plan_in_force(with_owners, *await _plan_in_force(runs, project_id, baseline_run_id))


@router.get(
    "/projects/{projectId}/optimization/inputs",
    response_model=AppliedInputs,
    summary="Every input the solver will use, where it came from, and what blocks a run",
)
async def get_inputs(project_id: ProjectId, runs: OptimizationRuns) -> AppliedInputs:
    """The confirmation sheet shown before a run (`ready` false while `blocking` is
    non-empty), with `appliedHash`: the inputs hash of the plan in force, if any."""
    project = await _project(runs, project_id)
    planning_input, sheet = await _sheet(runs, project)
    applied = await runs.applied_run(project_id)
    return AppliedInputs(
        rows=[
            InputRow(
                key=row.key, label=row.label, value=row.value, source=row.source, from_=row.from_
            )
            for row in sheet.rows
        ],
        ready=sheet.ready,
        blocking=sheet.blocking,
        missing=sheet.missing,
        hash=inputs_hash(planning_input),
        applied_hash=applied.inputs_hash if applied else None,
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
    """Freeze the inputs into a queued run; 422 with `missing` while the sheet isn't ready,
    409 while another run is active or when the inputs changed since `reviewedHash`."""
    body = body or RunCreate()
    project = await _project(runs, project_id)
    if not await runs.can_write(project_id):
        raise ForbiddenError("You do not have permission to optimize this project")

    if not await runs.lock_run_creation(project_id):  # another request is starting one
        raise ConflictError(ACTIVE_RUN_DETAIL)
    stale_after_s = request.app.state.settings.optimizer_stale_after_s
    active = await runs.active_run(project_id, stale_after_s)
    if active is not None:
        if not active.stale:
            raise ConflictError(ACTIVE_RUN_DETAIL)
        await runs.fail_run(active.id, RESTART_ERROR)  # orphaned by a restart

    planning_input, sheet = await _sheet(runs, project)
    # Fingerprint of the saved inputs, before this run's baseline, overrides and
    # remedy: exactly the `hash` GET …/optimization/inputs returns for the same data.
    saved_inputs_hash = inputs_hash(planning_input)
    # CON-019: the PM approved the sheet showing `reviewedHash`; never run other inputs.
    if body.reviewed_hash is not None and body.reviewed_hash != saved_inputs_hash:
        raise ConflictError(STALE_REVIEW_DETAIL)
    lifted = LIFTED_BY_REMEDY.get(body.remedy or "", ())
    blockers: list[Blocker] = [blocker for blocker in sheet.blockers if blocker.key not in lifted]
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
    planning_input = await _from_baseline(runs, project_id, planning_input, body.baseline_run_id)

    # The body exactly as sent (camelCase, ids and dates as strings): the run's setup.
    options = body.model_dump(mode="json", by_alias=True, exclude_none=True)
    planning_input = apply_remedy(
        apply_overrides(planning_input, options.get("overrides")), body.remedy
    )
    options["inputsHash"] = saved_inputs_hash
    options["solverSettings"] = run_settings(request.app.state.settings, planning_input)

    try:
        run_id = await runs.create(project_id, planning_input.to_snapshot(), options)
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
