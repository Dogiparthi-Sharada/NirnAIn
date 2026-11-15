"""Tests of mapping database errors to problem+json responses.

Purpose: RLS refusals, trigger messages, constraint violations and outages must reach the user as
the right status with a safe message.
How it fits: Exercises app/core/errors.py with fake asyncpg errors wrapped as SQLAlchemy raises
them; layer: tests.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    ServiceUnavailableError,
    UnprocessableError,
    map_db_error,
    pg_error_info,
)


def db_error(
    sqlstate: str | None,
    message: str = "boom",
    constraint: str | None = None,
    error_type: type[DBAPIError] = DBAPIError,
) -> DBAPIError:
    """Return a SQLAlchemy error wrapping an asyncpg error with the given SQLSTATE."""

    class _AsyncpgError(Exception):
        """Stand-in for asyncpg's error with its SQLSTATE fields."""

        pass

    cause = _AsyncpgError(message)
    cause.sqlstate = sqlstate  # type: ignore[attr-defined]
    cause.message = message  # type: ignore[attr-defined]
    cause.constraint_name = constraint  # type: ignore[attr-defined]

    class _AdaptedError(Exception):
        """Stand-in for the driver adapter's error carrying `pgcode`."""

        pgcode = sqlstate

    orig = _AdaptedError(f"<class 'asyncpg...'>: {message}")
    orig.__cause__ = cause
    return error_type("select 1", {}, orig)


@pytest.mark.parametrize(
    ("sqlstate", "expected", "status"),
    [
        ("42501", ForbiddenError, 403),
        ("P0001", UnprocessableError, 422),
        ("23505", ConflictError, 409),
        ("23514", UnprocessableError, 422),
        ("23503", UnprocessableError, 422),
        ("22001", UnprocessableError, 422),
        ("40001", ConflictError, 409),
        ("57014", ServiceUnavailableError, 503),
        ("08006", ServiceUnavailableError, 503),
        ("XX000", AppError, 500),
    ],
)
def test_map_db_error(sqlstate: str, expected: type[AppError], status: int) -> None:
    """Each SQLSTATE maps to its error type and status."""
    mapped = map_db_error(db_error(sqlstate))
    assert type(mapped) is expected
    assert mapped.status == status


def test_trigger_message_is_surfaced() -> None:
    """A trigger's raised message reaches the user."""
    mapped = map_db_error(db_error("P0001", "projects.org_id is immutable"))
    assert mapped.detail == "projects.org_id is immutable"


def test_operational_error_without_sqlstate_is_503() -> None:
    """A lost connection without a SQLSTATE is 503."""
    assert map_db_error(db_error(None, error_type=OperationalError)).status == 503


def test_pg_error_info_extracts_constraint() -> None:
    """The SQLSTATE and constraint name are read from the wrapped error."""
    info = pg_error_info(db_error("23514", constraint="projects_check"))
    assert info.sqlstate == "23514"
    assert info.constraint == "projects_check"


def test_db_errors_render_as_problem(app: FastAPI) -> None:
    """Database errors render as problem+json; 500s hide the message."""

    @app.get("/raise/{code}")
    async def _raise(code: str) -> None:
        """Raise the database error with the given SQLSTATE."""
        raise db_error(code, "WBS parent belongs to a different project", "c1")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/raise/P0001")
        assert response.status_code == 422
        assert response.json()["detail"] == "WBS parent belongs to a different project"
        response = test_client.get("/raise/23505")
        assert response.status_code == 409
        assert response.json()["constraint"] == "c1"
        response = test_client.get("/raise/42501")
        assert response.status_code == 403
        response = test_client.get("/raise/XX000")
        assert response.status_code == 500
        assert "WBS parent" not in response.text


def test_pool_timeout_is_503(app: FastAPI) -> None:
    """A pool timeout is 503."""
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    @app.get("/pool")
    async def _pool() -> None:
        """Raise the pool's timeout."""
        raise PoolTimeout("QueuePool limit reached")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        assert test_client.get("/pool").status_code == 503
