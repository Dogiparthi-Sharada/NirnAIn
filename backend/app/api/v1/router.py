"""The `/api/v1` router that collects every resource router.

Purpose: Mount all version-1 endpoints under one prefix so the app includes a single router.
How it fits: Included by app/main.py next to the unversioned health router; layer: api.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    ai,
    assistant,
    changes,
    dashboard,
    documents,
    drafts,
    intake,
    me,
    notifications,
    optimization,
    orgs,
    overview,
    plan,
    planning,
    projects,
    team,
    wbs,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(me.router)
api_router.include_router(orgs.router)
api_router.include_router(projects.router)
api_router.include_router(dashboard.router)
api_router.include_router(notifications.router)
api_router.include_router(optimization.router)
api_router.include_router(intake.router)
api_router.include_router(wbs.router)
api_router.include_router(planning.router)
api_router.include_router(plan.router)
api_router.include_router(changes.router)
api_router.include_router(drafts.router)
api_router.include_router(overview.router)
api_router.include_router(ai.router)
api_router.include_router(assistant.router)
api_router.include_router(documents.router)
api_router.include_router(team.router)
