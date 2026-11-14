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
