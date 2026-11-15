"""Fake database connections that record SQL and answer queries with scripted rows.

Purpose: Test repositories' SQL and row mapping without a database: each `execute` compiles the
statement for Postgres, records it with its parameters and returns the next scripted result.
How it fits: Used by the repository tests (test_repositories.py, test_optimization_repo.py, ...)
with the real repository classes; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.repositories.optimization import (
    ProjectContext,
)


class Result:
    """Scripted query result whose rows expose `_mapping`, like SQLAlchemy rows."""

    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 1) -> None:
        """Wrap the row dictionaries; `rowcount` is what an UPDATE or DELETE reports."""
        self._rows = [SimpleNamespace(_mapping=row) for row in rows]
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        """Return every row."""
        return self._rows

    def first(self) -> Any:
        """Return the first row, or None."""
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        """Return the only row (the test fails unless there is exactly one)."""
        assert len(self._rows) == 1
        return self._rows[0]


class Conn:
    """Connection that records compiled SQL and parameters and replays scripted results."""

    def __init__(self, *results: Result) -> None:
        """Queue the results to return, in order."""
        self.results = list(results)
        self.sql: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, stmt: Any) -> Result:
        """Record the statement as Postgres SQL with its parameters and return the next result."""
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.sql.append(str(compiled))
        self.params.append(dict(compiled.params))
        return self.results.pop(0) if self.results else Result([])


class UniqueViolationError(Exception):
    """Driver error carrying Postgres's unique-violation SQLSTATE."""

    sqlstate = "23505"


class RaisingConn(Conn):
    """Connection whose every statement fails with an IntegrityError caused by `cause`."""

    def __init__(self, cause: Exception) -> None:
        """Fail every statement with `cause` as the driver error."""
        super().__init__()
        self.cause = cause

    async def execute(self, stmt: Any) -> Result:
        """Raise the integrity error instead of running the statement."""
        raise IntegrityError("INSERT", {}, self.cause)


class Rows(Result):
    """Result whose rows support attribute access, like SQLAlchemy rows."""

    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 1) -> None:
        """Wrap the row dictionaries as attribute-access rows."""
        super().__init__(rows, rowcount)
        self._rows = [_Row(row) for row in rows]

    def scalar(self) -> Any:
        """Return the first column of the first row, or None."""
        return next(iter(self._rows[0]._mapping.values())) if self._rows else None


class _Row(SimpleNamespace):
    """A row with both attribute access and `_mapping`."""

    def __init__(self, mapping: dict[str, Any]) -> None:
        """Expose the mapping's keys as attributes."""
        super().__init__(**mapping)
        self._mapping = mapping


def wbs_node_row(
    node_id: uuid.UUID, parent: uuid.UUID | None, level: int, code: str, **values: Any
) -> dict[str, Any]:
    """Return a `wbs_nodes_with_code` row named after its code; `values` override columns."""
    row = {
        "id": node_id,
        "parent_id": parent,
        "level": level,
        "name": f"Node {code}",
        "description": None,
        "duration_days": None,
        "effort_hours": None,
        "owner_employee_id": None,
        "code": code,
    }
    return row | values


def project_context() -> ProjectContext:
    """Return the CRM project's context (Oct 5, 2026 to Jan 29, 2027; $375,000)."""
    return ProjectContext(
        uuid.uuid4(), uuid.uuid4(), "CRM", date(2026, 10, 5), date(2027, 1, 29), 37_500_000
    )
