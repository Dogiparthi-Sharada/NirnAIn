"""The project's constraint set as the optimizer reads it (caller's RLS session).

Purpose: Turn the saved step-5 answers into solver constraints: the deadline policy follows the
PM's answer ('soft' can move, anything else is hard), outside specialists only when the PM said
'yes' (at the contractor rate or the default specialist estimate), and the project's own dates
and budget fill in whatever the constraint set leaves open.
How it fits: Used by app/repositories/planning_input.py when freezing a run's input and by
app/repositories/input_facts.py for the confirmation sheet; layer: repositories.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.optimization.model import Constraints
from app.optimization.remedies import DEFAULT_SPECIALIST_RATE_CENTS
from app.repositories.access import ProjectRef
from app.repositories.tables import constraint_sets


def build_constraints(
    constraint_set: sa.Row[Any] | None, project: ProjectRef, today: date
) -> tuple[Constraints, date]:
    """Return the solver constraints and the planning start for a project.

    Falls back to the project's own dates and budget; the start is the constraint set's start
    date, else the project's, else `today`.
    """
    start = (constraint_set.start_date if constraint_set else None) or project.start_date or today
    if constraint_set is None:
        # Nothing answered yet: hard deadline, no outside specialists.
        return (
            Constraints(
                budget_total_cents=project.budget_cents,
                must_finish_by=project.target_end_date,
                allow_contractors=False,
            ),
            start,
        )
    specialists_allowed = constraint_set.specialist_answer == "yes"
    rate = constraint_set.contractor_rate_cents
    if specialists_allowed and rate is None:
        rate = DEFAULT_SPECIALIST_RATE_CENTS
    budget = constraint_set.budget_total_cents
    return (
        Constraints(
            budget_total_cents=budget if budget is not None else project.budget_cents,
            labor_cap_cents=constraint_set.labor_cap_cents,
            budget_mode=str(constraint_set.budget_mode),  # type: ignore[arg-type]
            budget_penalty=float(constraint_set.budget_penalty),
            contingency_pct=float(constraint_set.contingency_pct),
            must_finish_by=constraint_set.must_finish_by or project.target_end_date,
            # Only an explicit 'soft' answer lets the deadline move; "Don't know" stays hard.
            deadline_mode="soft" if constraint_set.deadline_answer == "soft" else "hard",
            deadline_penalty_per_day=float(constraint_set.deadline_penalty_per_day),
            working_days_per_week=int(constraint_set.working_days_per_week),
            capacity_mode=str(constraint_set.capacity_mode),  # type: ignore[arg-type]
            capacity_penalty=float(constraint_set.capacity_penalty),
            skill_mode=str(constraint_set.skill_mode),  # type: ignore[arg-type]
            allow_contractors=specialists_allowed,
            contractor_rate_cents=rate,
        ),
        start,
    )


async def read_constraint_set(conn: AsyncConnection, project_id: uuid.UUID) -> sa.Row[Any] | None:
    """Return the project's constraint set row (None until step 5 is saved)."""
    return (
        await conn.execute(
            sa.select(constraint_sets).where(constraint_sets.c.project_id == project_id)
        )
    ).first()
