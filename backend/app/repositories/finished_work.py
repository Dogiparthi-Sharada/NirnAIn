"""Finished work packages, read while freezing the optimizer's input (caller's RLS session).

Purpose: Round 3, R2 "done work stays done": a 0-day package that an applied plan scheduled is
finished work, kept in every later plan at the dates the latest such plan gave it; a 0-day package
no applied plan scheduled has no usable duration and blocks the optimizer.
How it fits: Called by app/repositories/work_packages.py and the planning-input reader; reads
`optimization_runs.result.schedule`; layer: repositories.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, distinct_on
from sqlalchemy.ext.asyncio import AsyncConnection

from app.repositories.tables import optimization_runs


async def finished_work(
    connection: AsyncConnection, project_id: uuid.UUID, candidates: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[date, date]]:
    """Work package id -> (first day, last day) for the `candidates` (0-day packages) that
    an applied plan of the project scheduled, at the dates of the latest such plan."""
    if not candidates:
        return {}
    runs = optimization_runs
    item = (
        sa.func.jsonb_array_elements(runs.c.result["schedule"])
        .table_valued(sa.column("value", JSONB))
        .lateral("item")
    )
    node_id = item.c.value["wbsNodeId"].astext
    statement = (
        sa.select(
            node_id.label("node_id"),
            item.c.value["start"].astext.label("first_day"),
            item.c.value["end"].astext.label("last_day"),
        )
        .select_from(runs.join(item, sa.true()))
        .where(
            runs.c.project_id == project_id,
            runs.c.applied_at.is_not(None),
            sa.func.jsonb_typeof(runs.c.result["schedule"]) == "array",
            node_id.in_([str(candidate) for candidate in candidates]),
        )
        .ext(distinct_on(node_id))
        .order_by(node_id, runs.c.applied_at.desc())
    )
    return {
        uuid.UUID(row.node_id): (
            date.fromisoformat(row.first_day),
            date.fromisoformat(row.last_day),
        )
        for row in (await connection.execute(statement)).all()
    }
