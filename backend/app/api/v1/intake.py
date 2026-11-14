"""AI intake endpoints: start / poll analysis runs and review the extracted scope items.

All queries run under the caller's RLS session; only the background worker
(`app.jobs.intake`) writes results on a service connection. Once a plan version is applied
the requirements are frozen like every other input (409 "Plan vN is in force; record a
change on step 7"): new intake runs and scope-item edits wait for step 7, where an approved
change adds the requirements its notes bring.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status

from app.api.deps import Intaker, Intakes, ItemId, ProjectId, RunId
from app.core.db import UserDB
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.domain.intake import (
    IntakeRun,
    IntakeRunCreate,
    ScopeItem,
    ScopeItemCreate,
    ScopeItemUpdate,
)
from app.domain.team import ensure_unlocked
from app.jobs.intake import RESTART_ERROR
from app.repositories.intake import ActiveRunExistsError
from app.repositories.plan_lock import PlanLock

router = APIRouter(tags=["intake"])


def get_plan_lock(connection: UserDB) -> PlanLock:
    """The plan lock on the caller's RLS session (tests override this)."""
    return PlanLock(connection)


Locks = Annotated[PlanLock, Depends(get_plan_lock)]

ACTIVE_RUN_DETAIL = "AI intake is already running for this project."
NOTHING_TO_ANALYZE_DETAIL = "Paste project notes or upload a document first."


@router.post(
    "/projects/{projectId}/intake/runs",
    response_model=IntakeRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Analyze pasted notes and uploaded documents (extracted in the background)",
)
async def create_run(
    project_id: ProjectId,
    body: IntakeRunCreate,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    runs: Intakes,
    intaker: Intaker,
    locks: Locks,
) -> IntakeRun:
    """Queue a run over the pasted `text` plus every upload whose text was read."""
    if not await runs.project_exists(project_id):
        raise NotFoundError("Project not found")
    if not await runs.can_write(project_id):
        raise ForbiddenError("You do not have permission to analyze this project")
    ensure_unlocked(await locks.version(project_id))
    if not body.text and not await runs.has_readable_upload(project_id):
        raise UnprocessableError(NOTHING_TO_ANALYZE_DETAIL)

    stale_after_s = request.app.state.settings.intake_stale_after_s
    active = await runs.active_run(project_id, stale_after_s)
    if active is not None:
        if not active.stale:
            raise ConflictError(ACTIVE_RUN_DETAIL)
        await runs.fail_run(active.id, RESTART_ERROR)  # orphaned by a restart

    try:
        run_id = await runs.create(project_id, body.text)
    except ActiveRunExistsError as exc:  # lost a race with a concurrent request
        raise ConflictError(ACTIVE_RUN_DETAIL) from exc
    run = await runs.get(project_id, run_id)
    if run is None:
        raise ForbiddenError("The run was created but is not visible to you")

    # Background tasks run after the response, i.e. after the transaction has
    # committed, so the worker always finds the queued row.
    background.add_task(intaker.submit, run_id)
    response.headers["Location"] = f"/api/v1/projects/{project_id}/intake/runs/{run_id}"
    return run


@router.get(
    "/projects/{projectId}/intake/runs",
    response_model=list[IntakeRun],
    summary="The project's 20 most recent intake runs, newest first",
)
async def list_runs(project_id: ProjectId, runs: Intakes) -> list[IntakeRun]:
    """Run history for "Previous Intakes" (404 when the project is not visible)."""
    if not await runs.project_exists(project_id):
        raise NotFoundError("Project not found")
    return await runs.list_runs(project_id)


# Declared before /{runId} so "latest" isn't parsed as a run id.
@router.get(
    "/projects/{projectId}/intake/runs/latest",
    response_model=IntakeRun,
    summary="Latest intake run (404 when the project has none)",
)
async def latest_run(project_id: ProjectId, runs: Intakes) -> IntakeRun:
    """The newest run of the project."""
    run = await runs.latest(project_id)
    if run is None:
        raise NotFoundError("No intake runs yet")
    return run


@router.get(
    "/projects/{projectId}/intake/runs/{runId}",
    response_model=IntakeRun,
    summary="Get an intake run",
)
async def get_run(project_id: ProjectId, run_id: RunId, runs: Intakes) -> IntakeRun:
    """One run of the project (the `Location` of a created run)."""
    run = await runs.get(project_id, run_id)
    if run is None:
        raise NotFoundError("Intake run not found")
    return run


@router.get(
    "/projects/{projectId}/scope-items",
    response_model=list[ScopeItem],
    summary="Scope items extracted (or added) for a project",
)
async def list_scope_items(project_id: ProjectId, runs: Intakes) -> list[ScopeItem]:
    """Every item except removed ones, by category and order, with its evidence."""
    if not await runs.project_exists(project_id):
        raise NotFoundError("Project not found")
    return await runs.list_scope_items(project_id)


@router.post(
    "/projects/{projectId}/scope-items",
    response_model=ScopeItem,
    status_code=status.HTTP_201_CREATED,
    summary="Add a scope item by hand (accepted, appended to its category)",
)
async def create_scope_item(
    project_id: ProjectId, body: ScopeItemCreate, runs: Intakes, locks: Locks
) -> ScopeItem:
    """Add the user's own item: accepted, origin manual, no evidence ("Yours")."""
    if not await runs.project_exists(project_id):
        raise NotFoundError("Project not found")
    if not await runs.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this project's scope")
    ensure_unlocked(await locks.version(project_id))
    item = await runs.create_item(project_id, body.category, body.text)
    if item is None:
        raise ForbiddenError("The scope item was created but is not visible to you")
    return item


@router.patch(
    "/scope-items/{itemId}",
    response_model=ScopeItem,
    summary="Accept, edit, or remove a scope item",
)
async def update_scope_item(
    item_id: ItemId, body: ScopeItemUpdate, runs: Intakes, locks: Locks
) -> ScopeItem:
    """Partial update; text equal to `suggestedText` resolves a flagged item to stated."""
    ensure_unlocked(await locks.scope_item_version(item_id))
    patch = body.model_dump(exclude_unset=True)
    item = await runs.update_item(item_id, patch)
    if item is None:
        raise NotFoundError("Scope item not found")
    return item
