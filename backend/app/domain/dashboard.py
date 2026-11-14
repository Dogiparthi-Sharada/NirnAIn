"""API model of the organization dashboard's KPI cards.

Purpose: Summarise the projects the caller can see (counts, budget, at-risk) for the dashboard.
How it fits: Returned by `GET /orgs/{orgId}/dashboard` (app/api/v1/dashboard.py), filled by
app/repositories/projects.py under RLS; layer: domain.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from app.domain.common import CamelModel


class DashboardSummary(CamelModel):
    """KPI cards. Counts only projects visible to the caller, excluding archived."""

    total_projects: int
    on_track: int
    at_risk_or_delayed: int
    total_budget_cents: int
    created_this_month: int
