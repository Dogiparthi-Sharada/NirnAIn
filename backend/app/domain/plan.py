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
