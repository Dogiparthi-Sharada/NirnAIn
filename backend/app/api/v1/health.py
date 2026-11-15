"""Liveness and readiness endpoints for the hosting platform.

Purpose: Let Railway tell a running process (`/health`) from one that can serve requests
(`/health/ready` checks the database) without touching user data.
How it fits: Mounted directly by app/main.py (outside `/api/v1`); layer: api.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.core.db import get_engine
from app.core.errors import ServiceUnavailableError
from app.domain.common import CamelModel

logger = logging.getLogger("app.health")

router = APIRouter(prefix="/health", tags=["health"])


class Health(CamelModel):
    """Liveness answer: always `ok`, with the deployed version."""

    status: str
    version: str


class Readiness(CamelModel):
    """Readiness answer: `ok` once the database answers."""

    status: str
    database: str


@router.get("", response_model=Health, summary="Liveness (no dependencies)")
async def health(request: Request) -> Health:
    """Answer that the process is alive, with the deployed version."""
    return Health(status="ok", version=request.app.state.settings.version)


@router.get("/ready", response_model=Readiness, summary="Readiness (checks the database)")
async def ready(request: Request) -> Readiness:
    """Answer `ok` when the database accepts a trivial query; 503 otherwise."""
    # Infrastructure check: connects as the service user, touches no user data.
    engine = get_engine(request)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
    except Exception as exc:
        logger.warning("readiness check failed", extra={"error": type(exc).__name__})
        raise ServiceUnavailableError("Database is unreachable") from exc
    return Readiness(status="ok", database="ok")
