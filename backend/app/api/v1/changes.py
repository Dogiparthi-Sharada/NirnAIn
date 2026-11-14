"""Changes & Versions (step 7): record a change, answer its questions, approve or keep the plan.

The re-plan itself is C1's `POST /projects/{projectId}/optimization/runs` with
`{overrides, changeRequestId, baselineRunId}`; this module reads the change, derives
the overrides, and applies the proposed run on approval. People who join get an
organization employee reserved when the change is recorded (so the re-plan can staff
them); approval puts them on the team, a rejection or a "Don't bring them in" releases it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response, status

from app.ai.llm import LLM, make_client
from app.api.deps import ProjectId
from app.changes.compare import build_comparison
from app.changes.derive import InvalidEditError, build_impact, from_edits, requirements_to_add
from app.changes.llm import RULES_MODEL, ChangeDetector
from app.changes.questions import validate_answers
from app.changes.team import reserved_employees, unused_reservations
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


def get_change_repo(connection: UserDB) -> ChangeRepository:
    """Change repository on the caller's RLS session (tests override this)."""
    return ChangeRepository(connection)


def get_change_detector(request: Request) -> ChangeDetector:
    """One detector per app: Claude when ANTHROPIC_API_KEY is set, else the rules.

    Built lazily so the API starts without a key; tests override this dependency.
    """
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
    """The project, or 404 when not visible / 403 when the caller can't change it."""
    project = await changes.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if not await changes.can_write(project_id):
        raise ForbiddenError("You do not have permission to change this project")
    return project


async def _change(changes: ChangeRepository, change_id: uuid.UUID) -> ChangeRequest:
    """The change, or 404 (RLS hides other projects' changes)."""
    change = await changes.get(change_id)
    if change is None:
        raise NotFoundError("Change not found")
    return change


def _open(change: ChangeRequest) -> None:
    """409 once a change is approved or rejected: decisions are final."""
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
    """Record a change and find what it affects.

    Needs a plan in force (409 otherwise). Notes are read by Claude or the rules (the
    analysis runs inside the request, so the 202 response already carries the impact);
    edits and progress are taken as given (422 when they change nothing, or when a
    newcomer is already on the team, someone to remove isn't, or nobody would be left).
    The change is measured against the run in force (`baseRunId`).
    """
    project = await _writable_project(changes, project_id)
    in_force = await changes.in_force(project_id)
    if in_force is None:
        raise ConflictError(NO_PLAN)
    context = await changes.context(project, in_force, datetime.now(UTC).date())

    found: list[DetectedChange]
    if body.source == "notes":
        if not body.text:
            raise UnprocessableError("Paste the new meeting notes (or upload a file) first.")
        found, model = await detector.detect(body.text, context)
    else:
        if body.edits is None:
            raise UnprocessableError("Enter at least one change first.")
        try:
            found = from_edits(body.edits, context)
        except InvalidEditError as exc:
            raise UnprocessableError(str(exc)) from exc
        if not found:
            raise UnprocessableError(f"Nothing changed: these values match {context.plan_label}.")
        model = RULES_MODEL

    found = await changes.reserve_members(project, found)
    impact = build_impact(found, body.source, context, model)
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
    """The project's changes, newest first (readers may list; 404 when not visible)."""
    if await changes.project(project_id) is None:
        raise NotFoundError("Project not found")
    return await changes.list(project_id)


@router.get("/changes/{changeId}", response_model=ChangeRequest, summary="Get a change")
async def get_change(change_id: ChangeId, changes: Changes) -> ChangeRequest:
    """One change with its impact, answers and, once complete, the re-plan overrides."""
    return await _change(changes, change_id)


@router.patch(
    "/changes/{changeId}",
    response_model=ChangeRequest,
    summary="Answer the change's questions (returns the re-plan overrides once all are answered)",
)
async def answer_change(
    change_id: ChangeId, body: ChangeAnswers, changes: Changes
) -> ChangeRequest:
    """Save answers (validated against the questions' options; 422 otherwise).

    Changing an answer after a re-plan drops that proposal: it was solved with other values.
    """
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
    """Approve the re-plan: it becomes Plan vN+1 and the change's values become the inputs.

    Refused while the run is still solving (409), when it didn't solve (422), when it is
    already applied, or when another plan was applied since the change was recorded.
    """
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
    await changes.add_requirements(project.id, requirements_to_add(change.impact))
    await changes.release_members(project, unused_reservations(change.impact, overrides))
    await changes.decide(change_id, ChangeStatus.APPROVED, run_id)
    return await _change(changes, change_id)


@router.post(
    "/changes/{changeId}/reject",
    response_model=ChangeRequest,
    summary="Keep the plan in force; the change stays on record",
)
async def reject_change(change_id: ChangeId, changes: Changes) -> ChangeRequest:
    """Keep Plan vN: the change is closed as rejected and stays on record; employees
    reserved for its newcomers are released."""
    change = await _change(changes, change_id)
    project = await _writable_project(changes, change.project_id)
    _open(change)
    await changes.release_members(project, reserved_employees(change.impact))
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
    """Plan vN+1 against Plan vN per work package (409 until the re-plan has solved)."""
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
