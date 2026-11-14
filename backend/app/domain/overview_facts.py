"""Facts the per-project dashboard is built from.

Purpose: Keep what the database says (policy answers, progress counts, the applied run, risks,
notifications) apart from how the dashboard derives its figures from it.
How it fits: app/repositories/overview.py fills these under the caller's RLS session and
app/domain/overview.py builds the response from them; layer: domain.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.domain.common import ConstraintMode, ScopeStatus
from app.domain.notifications import Notification
from app.domain.overview_milestones import MilestoneFact
from app.domain.projects import Project


@dataclass(frozen=True, slots=True)
class PolicyFacts:
    """The project's `constraint_sets` row; None fields mean "not answered yet"."""

    budget_mode: ConstraintMode = ConstraintMode.HARD
    capacity_mode: ConstraintMode = ConstraintMode.SOFT
    skill_mode: ConstraintMode = ConstraintMode.HARD
    deadline_answer: str | None = None
    specialist_answer: str | None = None


@dataclass(frozen=True, slots=True)
class AppliedRunFacts:
    """The plan in force: its run, version, cost, completion date and stored result."""

    run_id: uuid.UUID
    version: int
    applied_at: datetime | None
    expected_cost_cents: int | None
    completion_date: date | None
    result: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ScopeRisk:
    """A risk scope item and its review status."""

    text: str
    status: ScopeStatus


@dataclass(frozen=True, slots=True)
class ProgressFacts:
    """Counts that tell how far through the workflow the project is."""

    scope_items: int = 0
    pending_scope_items: int = 0
    wbs_nodes: int = 0
    unconfirmed_ai_estimates: int = 0
    has_successful_run: bool = False


@dataclass(slots=True)
class OverviewFacts:
    """Everything the repository knows about the project for its overview."""

    project: Project
    policy: PolicyFacts | None = None
    progress: ProgressFacts = field(default_factory=ProgressFacts)
    milestones: list[MilestoneFact] = field(default_factory=list)
    applied_run: AppliedRunFacts | None = None
    scope_risks: list[ScopeRisk] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)
    notification_total: int = 0
    validation_issues: list[str] = field(default_factory=list)
    recommendation_reasons: list[str] = field(default_factory=list)
