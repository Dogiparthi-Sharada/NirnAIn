"""Per-project dashboard endpoint.

Purpose: Return everything the project overview shows in one call, derived from stored facts.
How it fits: Facts from app/repositories/overview.py, derived by app/domain/overview.py with today's
UTC date; layer: api.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.deps import Overviews, ProjectId, Projects
from app.core.errors import NotFoundError
from app.core.security import CurrentPrincipal
from app.domain.overview import ProjectOverview, build_overview

router = APIRouter(tags=["dashboard"])


@router.get(
    "/projects/{projectId}/overview",
    response_model=ProjectOverview,
    summary="Everything the per-project dashboard shows",
)
async def get_project_overview(
    project_id: ProjectId,
    principal: CurrentPrincipal,
    projects: Projects,
    overviews: Overviews,
) -> ProjectOverview:
    """Return the project's overview as the caller sees it; 404 when the project is not visible."""
    project = await projects.get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    facts = await overviews.facts(project, principal.user_id)
    return build_overview(facts, datetime.now(UTC).date())
