"""Shared SQL for background-job rows (optimization runs, intake runs, AI jobs).

Purpose: All three job tables decide "orphaned by a restart" the same way, so the rule lives once.
How it fits: Used by app/repositories/optimization.py, optimization_store.py, intake.py,
intake_jobs.py and ai_jobs.py; layer: repositories.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import sqlalchemy as sa


def started_before(table: sa.Table, seconds: int) -> sa.ColumnElement[bool]:
    """Filter for job rows started (or, if never started, queued) more than `seconds` ago.

    A job still queued or running after that long was orphaned by a server restart.
    """
    cutoff = sa.func.now() - sa.func.make_interval(0, 0, 0, 0, 0, 0, seconds)
    return sa.func.coalesce(table.c.started_at, table.c.created_at) < cutoff
