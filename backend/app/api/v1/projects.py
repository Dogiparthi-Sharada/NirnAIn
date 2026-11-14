"""Project endpoints: list, create, read, update and delete projects.

Purpose: Manage the projects of an organization while RLS decides what the caller may see and
change; the explicit checks turn RLS outcomes into clear 404 / 403 / 422 answers.
How it fits: Uses app/repositories/projects.py and app/repositories/orgs.py; other routers reuse
`ensure_org_visible`; layer: api.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import OrgId, Orgs, ProjectId, Projects
from app.core.errors import ForbiddenError, NotFoundError, UnprocessableError
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, Cursor
from app.core.security import CurrentPrincipal
from app.domain.common import Page, ProjectStatus
from app.domain.projects import Project, ProjectCreate, ProjectUpdate

router = APIRouter(tags=["projects"])


async def ensure_org_visible(orgs: Orgs, org_id: uuid.UUID) -> None:
    """Raise 404 unless the caller is a member of the organization.

    Non-members can't see the organization row under RLS, so "no access" reads as "not found".
    """
    # Non-members can't see the organization row under RLS; answer 404 rather
    # than an empty list so the client can tell "no access" from "no data".
    if not await orgs.is_visible(org_id):
        raise NotFoundError("Organization not found")


async def _ensure_pm_is_member(orgs: Orgs, org_id: uuid.UUID, pm_user_id: uuid.UUID | None) -> None:
    """Raise 422 when the chosen project manager is not a member of the organization."""
    if pm_user_id is not None and not await orgs.is_member(org_id, pm_user_id):
        raise UnprocessableError("pmUserId must be a member of the organization")


@router.get(
    "/orgs/{orgId}/projects",
    response_model=Page[Project],
    summary="List projects in an organization",
)
async def list_projects(
    org_id: OrgId,
    orgs: Orgs,
    projects: Projects,
    status_filter: Annotated[
        list[ProjectStatus] | None,
        Query(
            alias="status",
            description="Filter by status (repeatable). Archived projects are hidden "
            "unless requested explicitly.",
        ),
    ] = None,
    search: Annotated[
        str | None, Query(alias="q", max_length=200, description="Search name or code")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[Project]:
    """Return a page of the organization's projects, newest first, by status and search."""
    await ensure_org_visible(orgs, org_id)
    items, next_cursor = await projects.list_page(
        org_id,
        statuses=status_filter,
        search=search.strip() if search and search.strip() else None,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
    )
    return Page[Project](items=items, next_cursor=next_cursor.encode() if next_cursor else None)


@router.post(
    "/orgs/{orgId}/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(
    org_id: OrgId,
    body: ProjectCreate,
    response: Response,
    principal: CurrentPrincipal,
    orgs: Orgs,
    projects: Projects,
) -> Project:
    """Create a project in the organization and return it with its `Location`.

    Returns 404 for a non-member, 422 when the PM is not a member.
    """
    await ensure_org_visible(orgs, org_id)
    await _ensure_pm_is_member(orgs, org_id, body.pm_user_id)
    project_id = await projects.create(org_id, principal.user_id, body.values())
    project = await projects.get(project_id)
    if project is None:  # inserted but not readable: should not happen for the creator
        raise ForbiddenError("The project was created but is not visible to you")
    response.headers["Location"] = f"/api/v1/projects/{project.id}"
    return project


@router.get("/projects/{projectId}", response_model=Project, summary="Get a project")
async def get_project(project_id: ProjectId, projects: Projects) -> Project:
    """Return the project; 404 when it does not exist or is not visible."""
    project = await projects.get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


@router.patch("/projects/{projectId}", response_model=Project, summary="Update a project")
async def update_project(
    project_id: ProjectId, body: ProjectUpdate, orgs: Orgs, projects: Projects
) -> Project:
    """Apply a partial update and return the project.

    Returns 422 for a non-member PM or an end date before the start, 403 when the caller can read
    but not write the project.
    """
    existing = await projects.get(project_id)
    if existing is None:
        raise NotFoundError("Project not found")

    changes = body.changes()
    if "pm_user_id" in changes:
        await _ensure_pm_is_member(orgs, existing.org_id, changes["pm_user_id"])
    start = changes.get("start_date", existing.start_date)
    end = changes.get("target_end_date", existing.target_end_date)
    if start is not None and end is not None and end < start:
        raise UnprocessableError("targetEndDate must be on or after startDate")

    if not await projects.update(project_id, changes):
        # Readable but the UPDATE policy filtered it out: caller can't write.
        raise ForbiddenError("You do not have permission to update this project")
    updated = await projects.get(project_id)
    if updated is None:
        raise NotFoundError("Project not found")
    return updated


@router.delete(
    "/projects/{projectId}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a project (hard delete)",
)
async def delete_project(project_id: ProjectId, projects: Projects) -> Response:
    """Delete the project for good; 404 when hidden, 403 when the caller may not delete it."""
    if await projects.get(project_id) is None:
        raise NotFoundError("Project not found")
    if not await projects.delete(project_id):
        raise ForbiddenError("You do not have permission to delete this project")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
