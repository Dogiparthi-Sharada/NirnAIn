from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Response, status

from app.api.deps import NodeId, ProjectId, Wbs
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.domain.team import ensure_unlocked
from app.domain.wbs import (
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
    if owner_id is not None and not await wbs.is_active_employee(org_id, owner_id):
        raise UnprocessableError("ownerEmployeeId must be an active employee of the organization")


async def _writable_node(wbs: Wbs, node_id: uuid.UUID) -> NodeRef:
    node = await wbs.node(node_id)
    if node is None:
        raise NotFoundError("WBS node not found")
    if not await wbs.can_write(node.project_id):
        raise ForbiddenError("You do not have permission to edit this WBS")
    ensure_unlocked(await wbs.applied_version(node.project_id))
    return node


@router.get(
    "/projects/{projectId}/wbs",
    response_model=list[WbsNode],
    summary="WBS of a project, in tree order",
)
async def list_wbs(project_id: ProjectId, wbs: Wbs) -> list[WbsNode]:
    if await wbs.project(project_id) is None:
        raise NotFoundError("Project not found")
    return await wbs.tree(project_id)


@router.post(
    "/projects/{projectId}/wbs/nodes",
    response_model=WbsNode,
    status_code=status.HTTP_201_CREATED,
    summary="Add a WBS node (appended after its siblings)",
)
async def create_node(project_id: ProjectId, body: WbsNodeCreate, wbs: Wbs) -> WbsNode:
    project = await wbs.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if not await wbs.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this WBS")
    ensure_unlocked(await wbs.applied_version(project_id))

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
    if await wbs.project(project_id) is None:
        raise NotFoundError("Project not found")
    if not await wbs.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this WBS")
    ensure_unlocked(await wbs.applied_version(project_id))
    await wbs.confirm_estimates(project_id, body.node_ids if body else None)
    return await wbs.tree(project_id)


@router.patch("/wbs/nodes/{nodeId}", response_model=WbsNode, summary="Update a WBS node")
async def update_node(node_id: NodeId, body: WbsNodeUpdate, wbs: Wbs) -> WbsNode:
    ref = await _writable_node(wbs, node_id)
    changes = body.changes()
    if changes.get("owner_employee_id") is not None:
        project = await wbs.project(ref.project_id)
        if project is None:
            raise NotFoundError("Project not found")
        await _ensure_owner(wbs, project.org_id, changes["owner_employee_id"])
    if not await wbs.update(node_id, changes):
        raise ForbiddenError("You do not have permission to edit this WBS")
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
    await _writable_node(wbs, node_id)
    if not await wbs.delete(node_id):
        raise ForbiddenError("You do not have permission to edit this WBS")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
