"""Token use of every Claude call (table `ai_usage_events`), written on the service connection.

Purpose: Intake runs and AI jobs kept their tokens on their own rows, but reading a project draft,
finding what changed on step 7 and the planning assistant recorded nothing (bug 6). Every feature
now records one row per successful Claude call: feature, model, input and output tokens, and the
workspace, project and user it served. Recording is best effort: a failure is logged and never
reaches the request or job that made the call.
How it fits: `record_ai_usage` is called by app/jobs/intake.py, app/jobs/ai_jobs.py and the
endpoints in app/api/v1 (drafts.py, changes.py, assistant.py) with a factory of service-connection
transactions (`engine.begin`, wired in app/main.py and app/api/deps.py); users only read the rows
(RLS, migration 20261116000100); layer: repositories.
Created: 2026-11-15 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.llm import Usage
from app.repositories.tables import ai_usage_events, organization_members, projects

logger = logging.getLogger("app.ai_usage")

AiFeature = Literal[
    "project_draft", "intake", "wbs_generate", "wbs_validate", "change_detection", "assistant"
]
# Each use opens one transaction on the service connection (bypasses RLS), e.g. `engine.begin`.
ServiceConnections = Callable[[], AbstractAsyncContextManager[AsyncConnection]]


@dataclass(frozen=True, slots=True)
class AiUsageEvent:
    """One successful Claude call: the feature, the model, its tokens and whom it served.

    The workspace is looked up from the project (or the user) when it isn't given.
    """

    feature: AiFeature
    model: str
    usage: Usage
    project_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


class AiUsageRepository:
    """Writes usage rows on a service connection; users only read them (RLS)."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the trusted service connection."""
        self._conn = conn

    async def _org_of(self, event: AiUsageEvent) -> uuid.UUID | None:
        """The event's workspace: given, else the project's, else the user's first workspace."""
        if event.org_id is not None:
            return event.org_id
        stmt: sa.Select[Any]
        if event.project_id is not None:
            stmt = sa.select(projects.c.org_id).where(projects.c.id == event.project_id)
        elif event.user_id is not None:
            # A question asked outside any project counts for the user's oldest workspace (the one
            # sign-up created for them), the same answer every time.
            members = organization_members
            stmt = (
                sa.select(members.c.org_id)
                .where(members.c.user_id == event.user_id)
                .order_by(members.c.created_at, members.c.org_id)
                .limit(1)
            )
        else:
            return None
        org_id: uuid.UUID | None = (await self._conn.execute(stmt)).scalar()
        return org_id

    async def add(self, event: AiUsageEvent) -> bool:
        """Insert the call's row; False (nothing written) when its workspace can't be found."""
        org_id = await self._org_of(event)
        if org_id is None:
            return False
        await self._conn.execute(
            sa.insert(ai_usage_events).values(
                id=uuid.uuid4(),
                org_id=org_id,
                project_id=event.project_id,
                user_id=event.user_id,
                feature=event.feature,
                model=event.model,
                input_tokens=max(event.usage.input_tokens, 0),
                output_tokens=max(event.usage.output_tokens, 0),
            )
        )
        return True


async def record_ai_usage(connections: ServiceConnections | None, event: AiUsageEvent) -> bool:
    """Record one Claude call on its own service-connection transaction; never raises.

    Returns whether a row was written. Without a database (`connections` None) nothing is.
    """
    if connections is None:
        return False
    try:
        async with connections() as conn:
            return await AiUsageRepository(conn).add(event)
    except Exception:
        # The call already succeeded and its answer is what the user waits for: losing one
        # usage row is better than failing their request or job.
        logger.warning(
            "could not record AI usage",
            extra={"feature": event.feature, "model": event.model},
            exc_info=True,
        )
        return False
