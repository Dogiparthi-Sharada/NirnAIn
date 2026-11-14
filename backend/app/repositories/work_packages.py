"""Work packages of a project and the ones the optimizer can't plan (caller's RLS session).

Purpose: Work packages are level-3 WBS nodes and level-2 nodes without children (WBS-003, WBS-004):
a package with no duration, or 0 days that no applied plan scheduled, is never left out of a plan
silently; it blocks the optimizer until a person enters its duration on step 4.
How it fits: Used by the planning-input reader, the optimization inputs sheet and the WBS checks;
uses app/repositories/finished_work.py; layer: repositories.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.repositories.finished_work import finished_work
from app.repositories.tables import wbs_nodes_with_code

DELIVERABLE_LEVEL = 2
WORK_PACKAGE_LEVEL = 3


def work_packages(nodes: Sequence[sa.Row[Any]]) -> list[sa.Row[Any]]:
    """The work packages among `nodes` (rows with id, parent_id and level), in their order."""
    parents = {node.parent_id for node in nodes if node.parent_id is not None}
    return [
        node
        for node in nodes
        if node.level == WORK_PACKAGE_LEVEL
        or (node.level == DELIVERABLE_LEVEL and node.id not in parents)
    ]


async def packages_without_duration(
    connection: AsyncConnection, project_id: uuid.UUID
) -> list[str]:
    """'code name' of every work package the optimizer can't plan, in WBS order: no
    duration, or 0 days without being finished work."""
    view = wbs_nodes_with_code
    nodes = (
        await connection.execute(
            sa.select(
                view.c.id,
                view.c.parent_id,
                view.c.level,
                view.c.code,
                view.c.name,
                view.c.duration_days,
            )
            .where(view.c.project_id == project_id)
            .order_by(view.c.path)
        )
    ).all()
    packages = work_packages(nodes)
    finished = await finished_work(
        connection, project_id, [node.id for node in packages if node.duration_days == 0]
    )
    return [
        f"{node.code} {node.name}"
        for node in packages
        if not node.duration_days and node.id not in finished
    ]
