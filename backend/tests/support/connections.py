"""Moved test support."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy.dialects import postgresql

from app.repositories.optimization import (
    ProjectContext,
)


class Result:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 1) -> None:
        self._rows = [SimpleNamespace(_mapping=r) for r in rows]
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        assert len(self._rows) == 1
        return self._rows[0]


class Conn:
    def __init__(self, *results: Result) -> None:
        self.results = list(results)
        self.sql: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, stmt: Any) -> Result:
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.sql.append(str(compiled))
        self.params.append(dict(compiled.params))
        return self.results.pop(0) if self.results else Result([])


class Rows(Result):
    """Result whose rows support attribute access, like SQLAlchemy rows."""

    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 1) -> None:
        super().__init__(rows, rowcount)
        self._rows = [_Row(r) for r in rows]

    def scalar(self) -> Any:
        return next(iter(self._rows[0]._mapping.values())) if self._rows else None


class _Row(SimpleNamespace):
    def __init__(self, mapping: dict[str, Any]) -> None:
        super().__init__(**mapping)
        self._mapping = mapping


def project_ctx() -> ProjectContext:
    return ProjectContext(
        uuid.uuid4(), uuid.uuid4(), "CRM", date(2026, 10, 5), date(2027, 1, 29), 37_500_000
    )
