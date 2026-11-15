"""Organization dashboard endpoint: the KPI cards.

Purpose: Show the caller's organization at a glance (projects, at risk, budget, new this month).
How it fits: Reads app/repositories/projects.py through `Projects`; layer: api.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import OrgId, Orgs, Projects
from app.api.v1.projects import ensure_org_visible
from app.domain.dashboard import DashboardSummary

router = APIRouter(tags=["dashboard"])


@router.get(
    "/orgs/{orgId}/dashboard",
    response_model=DashboardSummary,
    summary="Dashboard KPI summary (excludes archived projects)",
)
async def get_dashboard(org_id: OrgId, orgs: Orgs, projects: Projects) -> DashboardSummary:
    """Return the KPI summary of the organization's non-archived projects; 404 if not a member."""
    await ensure_org_visible(orgs, org_id)
    return await projects.dashboard(org_id)
