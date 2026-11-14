"""API model of a project's applied plan versions (Plan v1, v2, ...).

Applying a successful optimization run (`public.apply_optimization_run`) gives it the
project's next version number. `GET /projects/{projectId}/plan-versions` (app/api/v1/plan.py)
lists them through app/repositories/plan.py, and the frontend mirrors the model in
src/lib/changes.ts for Changes & Versions. The apply endpoint answers with the run itself
(`app.domain.optimization.OptimizationRun`) and exports are files, so neither needs a model
here. JSON is camelCase (CamelModel).
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
