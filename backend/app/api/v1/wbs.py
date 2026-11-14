"""WBS endpoints: the tree, the built-in checks, adding, editing, moving and deleting nodes.

Purpose: Let the PM build the work breakdown structure on step 4 (root, deliverables, work packages
up to level 3), confirm AI estimates and see the free built-in checks after every edit.
How it fits: Uses app/repositories/wbs.py; the checks come from app/ai/wbs_validate.py (only the AI
review in app/api/v1/ai.py costs tokens); layer: api.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Response, status

from app.ai.wbs_validate import check_structure
from app.api.deps import NodeId, ProjectId, Wbs
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.domain.wbs import (
    WbsCheckIssue,
    WbsChecks,
    WbsConfirmEstimates,
    WbsMove,
    WbsNode,
    WbsNodeCreate,
    WbsNodeUpdate,
    reorder,
)
from app.repositories.wbs import NodeRef

router = APIRouter(tags=["wbs"])

MAX_LEVEL = 3


async def _ensure_owner(wbs: Wbs, org_id: uuid.UUID, owner_id: uuid.UUID | None) -> None:
    """Raise 422 unless the owner is an active employee of the organization."""
    if owner_id is not None and not await wbs.is_active_employee(org_id, owner_id):
        raise UnprocessableError("ownerEmployeeId must be an active employee of the organization")


async def _writable_node(wbs: Wbs, node_id: uuid.UUID) -> NodeRef:
    """Return the node; 404 when it is hidden, 403 when the caller can't edit the project's WBS."""
    node = await wbs.node(node_id)
    if node is None:
        raise NotFoundError("WBS node not found")
    if not await wbs.can_write(node.project_id):
        raise ForbiddenError("You do not have permission to edit this WBS")
    return node


@router.get(
    "/projects/{projectId}/wbs",
    response_model=list[WbsNode],
    summary="WBS of a project, in tree order",
)
async def list_wbs(project_id: ProjectId, wbs: Wbs) -> list[WbsNode]:
    """Return the project's WBS in tree order; 404 when the project is hidden."""
    if await wbs.project(project_id) is None:
        raise NotFoundError("Project not found")
    return await wbs.tree(project_id)


@router.get(
    "/projects/{projectId}/wbs/checks",
    response_model=WbsChecks,
    summary="Built-in checks on the current WBS, team and constraints (no AI, instant)",
)
async def wbs_checks(project_id: ProjectId, wbs: Wbs) -> WbsChecks:
    """The deterministic part of AI Validate, run now on live data: free, so the builder
    re-runs it after every edit. Only the AI review (`POST …/wbs/validate`) costs tokens."""
    now = datetime.now(UTC)
    checks_input = await wbs.check_input(project_id, now.date())
    if checks_input is None:
        raise NotFoundError("Project not found")
    return WbsChecks(
        issues=[
            WbsCheckIssue(type=issue.type, text=issue.text)
            for issue in check_structure(checks_input)
        ],
        checked_at=now,
        wbs_changed_at=await wbs.changed_at(project_id),
    )


@router.post(
    "/projects/{projectId}/wbs/nodes",
    response_model=WbsNode,
    status_code=status.HTTP_201_CREATED,
    summary="Add a WBS node (appended after its siblings)",
)
async def create_node(project_id: ProjectId, body: WbsNodeCreate, wbs: Wbs) -> WbsNode:
    """Add a node under its parent (or as the root) after its siblings and return it.

    Returns 409 for a second root, 422 for a parent in another project or below level 3, or an owner
    who is not an active employee.
    """
    project = await wbs.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if not await wbs.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this WBS")

    if body.parent_id is None:
        if await wbs.has_root(project_id):
            raise ConflictError("This project already has a root WBS node")
        level = 1
    else:
        parent = await wbs.node(body.parent_id)
        if parent is None or parent.project_id != project_id:
            raise UnprocessableError("parentId must be a WBS node of this project")
        if parent.level >= MAX_LEVEL:
            raise UnprocessableError("Work packages (level 3) cannot have children")
        level = parent.level + 1
    await _ensure_owner(wbs, project.org_id, body.owner_employee_id)

    node_id = await wbs.create(project_id, body.parent_id, level, body.values())
    node = await wbs.get(node_id)
    if node is None:
        raise ForbiddenError("The WBS node was created but is not visible to you")
    return node


@router.post(
    "/projects/{projectId}/wbs/confirm-estimates",
    response_model=list[WbsNode],
    summary="Confirm AI effort estimates (all of them when nodeIds is omitted); returns the tree",
)
async def confirm_estimates(
    project_id: ProjectId,
    wbs: Wbs,
    body: Annotated[WbsConfirmEstimates | None, Body()] = None,
) -> list[WbsNode]:
    """Confirm AI duration estimates (all of them when `nodeIds` is omitted) and return the tree."""
    if await wbs.project(project_id) is None:
        raise NotFoundError("Project not found")
    if not await wbs.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this WBS")
    await wbs.confirm_estimates(project_id, body.node_ids if body else None)
    return await wbs.tree(project_id)


async def _org_id(wbs: Wbs, project_id: uuid.UUID) -> uuid.UUID:
    """Return the organization of the project; 404 when it is hidden."""
    project = await wbs.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project.org_id


@router.patch(
    "/wbs/nodes/{nodeId}",
    response_model=WbsNode,
    summary="Update a WBS node (skills: the names it needs, replacing the current ones)",
)
async def update_node(node_id: NodeId, body: WbsNodeUpdate, wbs: Wbs) -> WbsNode:
    """Apply a partial update to a node (skills replace the current ones) and return it."""
    ref = await _writable_node(wbs, node_id)
    changes = body.changes()
    if changes.get("owner_employee_id") is not None:
        org_id = await _org_id(wbs, ref.project_id)
        await _ensure_owner(wbs, org_id, changes["owner_employee_id"])
    if changes and not await wbs.update(node_id, changes):
        raise ForbiddenError("You do not have permission to edit this WBS")
    if body.skills is not None:
        await wbs.set_skills(await _org_id(wbs, ref.project_id), node_id, body.skills)
    node = await wbs.get(node_id)
    if node is None:
        raise NotFoundError("WBS node not found")
    return node


@router.post(
    "/wbs/nodes/{nodeId}/move",
    response_model=list[WbsNode],
    summary="Move a WBS node to a position among its siblings; returns the whole tree",
)
async def move_node(node_id: NodeId, body: WbsMove, wbs: Wbs) -> list[WbsNode]:
    """Move a node to a position among its siblings and return the tree; the root can't move."""
    ref = await _writable_node(wbs, node_id)
    if ref.parent_id is None:
        raise UnprocessableError("The root WBS node can't be moved")
    siblings = await wbs.sibling_ids(ref.project_id, ref.parent_id)
    await wbs.set_order(reorder(siblings, node_id, body.position))
    return await wbs.tree(ref.project_id)


@router.delete(
    "/wbs/nodes/{nodeId}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a WBS node and its children",
)
async def delete_node(node_id: NodeId, wbs: Wbs) -> Response:
    """Delete a node with its children; 403 when the caller can't edit the WBS."""
    await _writable_node(wbs, node_id)
    if not await wbs.delete(node_id):
        raise ForbiddenError("You do not have permission to edit this WBS")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
