"""API model of a project's applied plan versions (Plan v1, v2, ...).

Purpose: Applying a successful optimization run (`public.apply_optimization_run`) gives it the
project's next version number; this model lists those versions for Changes & Versions.
How it fits: `GET /projects/{projectId}/plan-versions` (app/api/v1/plan.py) lists them through
app/repositories/plan.py; the frontend mirrors it in src/lib/changes.ts; layer: domain.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from app.domain.common import CamelModel


class PlanVersion(CamelModel):
    """An applied optimization run: Plan v1, v2, ... of its project."""

    version: int
    run_id: uuid.UUID
    applied_at: datetime | None
    completion_date: date | None
    expected_cost_cents: int | None
    solver: str | None
    label: str
