"""Changes & Versions (step 7): record a change, answer its questions, approve or keep the plan.

The re-plan itself is C1's `POST /projects/{projectId}/optimization/runs` with
`{overrides, changeRequestId, baselineRunId}`; this module reads the change, derives
the overrides, and applies the proposed run on approval.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response, status

from app.ai.llm import LLM, make_client
from app.api.deps import ProjectId
from app.changes.compare import build_comparison
from app.changes.derive import InvalidEditError, build_impact, from_edits, validate_answers
from app.changes.llm import RULES_MODEL, ChangeDetector
from app.core.db import UserDB
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.domain.changes import (
    DECIDED,
    ChangeAnswers,
    ChangeApprove,
    ChangeComparison,
    ChangeCreate,
    ChangeRequest,
    ChangeStatus,
    DetectedChange,
)
from app.domain.common import SolverStatus
from app.repositories.access import ProjectRef
from app.repositories.changes import SUCCESS, ChangeRepository

router = APIRouter(tags=["changes"])

NO_PLAN = "No applied plan yet. Apply a plan on step 6 first. Changes are measured against it."
RUNNING = (SolverStatus.QUEUED, SolverStatus.RUNNING)


def get_change_repo(db: UserDB) -> ChangeRepository:
    return ChangeRepository(db)


def get_change_detector(request: Request) -> ChangeDetector:
    detector: ChangeDetector | None = getattr(request.app.state, "change_detector", None)
    if detector is None:
        settings = request.app.state.settings
        detector = ChangeDetector(LLM(make_client(settings), settings.ai_model))
        request.app.state.change_detector = detector
    return detector


Changes = Annotated[ChangeRepository, Depends(get_change_repo)]
Detector = Annotated[ChangeDetector, Depends(get_change_detector)]
ChangeId = Annotated[uuid.UUID, Path(alias="changeId")]


async def _writable_project(changes: ChangeRepository, project_id: uuid.UUID) -> ProjectRef:
    project = await changes.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if not await changes.can_write(project_id):
        raise ForbiddenError("You do not have permission to change this project")
    return project


async def _change(changes: ChangeRepository, change_id: uuid.UUID) -> ChangeRequest:
    change = await changes.get(change_id)
    if change is None:
        raise NotFoundError("Change not found")
    return change


def _open(change: ChangeRequest) -> None:
    if change.status in DECIDED:
        raise ConflictError(f"This change was already {change.status.value}.")


@router.post(
    "/projects/{projectId}/changes",
    response_model=ChangeRequest,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record a change and find what it affects",
)
async def create_change(
    project_id: ProjectId,
    body: ChangeCreate,
    response: Response,
    changes: Changes,
    detector: Detector,
) -> ChangeRequest:
    project = await _writable_project(changes, project_id)
    in_force = await changes.in_force(project_id)
    if in_force is None:
        raise ConflictError(NO_PLAN)
    ctx = await changes.context(project, in_force, datetime.now(UTC).date())

    found: list[DetectedChange]
    if body.source == "notes":
        if not body.text:
            raise UnprocessableError("Paste the new meeting notes (or upload a file) first.")
        found, model = await detector.detect(body.text, ctx)
    else:
        if body.edits is None:
            raise UnprocessableError("Enter at least one change first.")
        try:
            found = from_edits(body.edits, ctx)
        except InvalidEditError as exc:
            raise UnprocessableError(str(exc)) from exc
        if not found:
            raise UnprocessableError(f"Nothing changed: these values match {ctx.plan_label}.")
        model = RULES_MODEL

    impact = build_impact(found, body.source, ctx, model)
    change_id = await changes.create(project_id, body.source, body.text, impact, in_force.run_id)
    change = await changes.get(change_id)
    if change is None:
        raise ForbiddenError("The change was recorded but is not visible to you")
    response.headers["Location"] = f"/api/v1/changes/{change_id}"
    return change


@router.get(
    "/projects/{projectId}/changes",
    response_model=list[ChangeRequest],
    summary="Changes of a project, newest first",
)
async def list_changes(project_id: ProjectId, changes: Changes) -> list[ChangeRequest]:
    if await changes.project(project_id) is None:
        raise NotFoundError("Project not found")
    return await changes.list(project_id)


@router.get("/changes/{changeId}", response_model=ChangeRequest, summary="Get a change")
async def get_change(change_id: ChangeId, changes: Changes) -> ChangeRequest:
    return await _change(changes, change_id)


@router.patch(
    "/changes/{changeId}",
    response_model=ChangeRequest,
    summary="Answer the change's questions (returns the re-plan overrides once all are answered)",
)
async def answer_change(
    change_id: ChangeId, body: ChangeAnswers, changes: Changes
) -> ChangeRequest:
    change = await _change(changes, change_id)
    await _writable_project(changes, change.project_id)
    _open(change)
    questions = change.impact.questions if change.impact else []
    problem = validate_answers(questions, body.answers)
    if problem:
        raise UnprocessableError(problem)
    merged = {**change.answers, **body.answers}
    # A proposal made with other answers is stale.
    reset = change.proposed_run_id is not None and merged != change.answers
    await changes.save_answers(change_id, merged, reset_proposal=reset)
    return await _change(changes, change_id)


@router.post(
    "/changes/{changeId}/approve",
    response_model=ChangeRequest,
    summary="Approve the re-plan: it becomes the next plan version and the change is saved",
)
async def approve_change(
    change_id: ChangeId,
    changes: Changes,
    body: Annotated[ChangeApprove | None, Body()] = None,
) -> ChangeRequest:
    change = await _change(changes, change_id)
    project = await _writable_project(changes, change.project_id)
    _open(change)
    run_id = change.proposed_run_id or (body.run_id if body else None)
    if run_id is None:
        raise ConflictError("Re-plan first: there is no proposed plan to approve yet.")
    run = await changes.run(run_id)
    if run is None or run.project_id != change.project_id:
        raise NotFoundError("Proposed plan not found")
    if run.status in RUNNING:
        raise ConflictError("The re-plan is still running.")
    if run.status not in SUCCESS:
        raise UnprocessableError("Only a successful run can be applied.")
    if run.applied:
        raise ConflictError("This plan is already applied.")
    overrides = change.overrides
    if overrides is None:
        raise ConflictError("Answer every question first.")
    in_force = await changes.in_force(change.project_id)
    if change.base_run_id is not None and (
        in_force is None or in_force.run_id != change.base_run_id
    ):
        raise ConflictError(
            "Another plan was applied since this change was recorded. Record the change again."
        )

    await changes.apply_run(run_id)
    await changes.write_overrides(project, overrides)
    await changes.decide(change_id, ChangeStatus.APPROVED, run_id)
    return await _change(changes, change_id)


@router.post(
    "/changes/{changeId}/reject",
    response_model=ChangeRequest,
    summary="Keep the plan in force; the change stays on record",
)
async def reject_change(change_id: ChangeId, changes: Changes) -> ChangeRequest:
    change = await _change(changes, change_id)
    await _writable_project(changes, change.project_id)
    _open(change)
    await changes.decide(change_id, ChangeStatus.REJECTED, None)
    return await _change(changes, change_id)


@router.get(
    "/changes/{changeId}/compare",
    response_model=ChangeComparison,
    summary="The proposed plan against the plan the change was measured against",
)
async def compare_change(
    change_id: ChangeId,
    changes: Changes,
    run_id: Annotated[uuid.UUID | None, Query(alias="runId")] = None,
) -> ChangeComparison:
    change = await _change(changes, change_id)
    proposed_id = change.proposed_run_id or run_id
    if change.base_run_id is None or proposed_id is None:
        raise ConflictError("Re-plan first: there is nothing to compare yet.")
    base = await changes.run_meta(change.base_run_id)
    proposed = await changes.run_meta(proposed_id)
    if base is None or proposed is None or proposed.project_id != change.project_id:
        raise NotFoundError("Plan not found")
    if proposed.status not in SUCCESS:
        raise ConflictError("The re-plan hasn't produced a plan yet.")
    base_rows = await changes.plan_rows(base.id)
    proposed_rows = await changes.plan_rows(proposed.id)
    people = {row.employee_id for row in [*base_rows, *proposed_rows] if row.employee_id}
    return build_comparison(
        base,
        base_rows,
        proposed,
        proposed_rows,
        await changes.nodes(change.project_id),
        await changes.employee_names(people),
        change.impact,
    )
