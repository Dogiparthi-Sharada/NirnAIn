"""Tests of the AI usage repository's edge cases: nothing to attribute the call to, no database.

Purpose: A usage row needs a workspace; when none can be found (an unknown project, no project and
no user) or the API runs without a database, nothing is written and nothing is raised. The feature
tests (drafts, intake, AI jobs, change detection, assistant) cover the rows that are written.
How it fits: Runs app/repositories/ai_usage.py on the recording connection of
tests/support/connections.py; layer: tests.
Created: 2026-11-15 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

from app.ai.llm import Usage
from app.repositories.ai_usage import AiUsageEvent, AiUsageRepository, record_ai_usage
from tests.support.connections import Conn, Rows


async def test_usage_without_a_workspace_or_a_database_writes_nothing() -> None:
    """An unknown project or no owner at all writes no row; without a database nothing runs."""
    unknown = AiUsageEvent("assistant", "claude-test", Usage(1, 1), project_id=uuid.uuid4())
    conn = Conn(Rows([]))
    assert await AiUsageRepository(conn).add(unknown) is False  # type: ignore[arg-type]
    assert len(conn.sql) == 1
    assert "FROM public.projects" in conn.sql[0]

    nobody = AiUsageEvent("assistant", "claude-test", Usage(1, 1))
    silent = Conn()
    assert await AiUsageRepository(silent).add(nobody) is False  # type: ignore[arg-type]
    assert silent.sql == []
    assert await record_ai_usage(None, unknown) is False
