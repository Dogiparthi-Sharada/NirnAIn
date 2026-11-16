"""A fake place to record AI token use: service-connection transactions that keep what was written.

Purpose: Let the feature tests check that each successful Claude call records exactly one
`ai_usage_events` row (feature, model, tokens, workspace, project, user) through the real
`record_ai_usage` and repository, and that a recording failure never fails the request or job.
How it fits: Passed as `usage_log` to the job runners and installed in place of
`app.api.deps.get_ai_usage_connections` for the endpoints; statements are compiled by the recording
connection of tests/support/connections.py; layer: tests.
Created: 2026-11-15 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from tests.support.connections import Conn, Rows

USAGE_INSERT = "INSERT INTO public.ai_usage_events"


class UsageLog:
    """Factory of fake service transactions; `rows` are the usage rows written so far.

    A workspace lookup (from the project or the user) answers `org_id`; with `fail` every
    transaction fails to open, like a database that is down.
    """

    def __init__(self, org_id: uuid.UUID | None = None, *, fail: bool = False) -> None:
        """Answer workspace lookups with `org_id`; fail every transaction when `fail` is set."""
        self.org_id = org_id or uuid.uuid4()
        self.fail = fail
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def __call__(self) -> AbstractAsyncContextManager[Conn]:
        """Open one fake transaction (what `engine.begin()` does for the real one)."""
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[Conn]:
        """Yield a recording connection; keep its statements once the block ends."""
        if self.fail:
            raise ConnectionError("the database is unavailable")
        conn = Conn(Rows([{"org_id": self.org_id}]))
        yield conn
        self.statements.extend(zip(conn.sql, conn.params, strict=True))

    @property
    def rows(self) -> list[dict[str, Any]]:
        """The values of every usage row inserted, in order."""
        return [params for sql, params in self.statements if sql.startswith(USAGE_INSERT)]

    def summary(self) -> list[tuple[Any, ...]]:
        """(feature, model, input tokens, output tokens) of every row, for short assertions."""
        return [
            (row["feature"], row["model"], row["input_tokens"], row["output_tokens"])
            for row in self.rows
        ]
