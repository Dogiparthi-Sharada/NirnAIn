"""Tests of the RLS session setup and the engine configuration.

Purpose: Every user request must run in a transaction as `authenticated` with the caller's claims,
roll back on errors, and the engine must work behind the Supabase pooler.
How it fits: Exercises app/core/db.py on a recording connection and engine; no live database needed;
layer: tests.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import db as db_module
from app.core.config import Settings
from app.core.db import apply_rls_context, create_engine, get_service_db, get_user_db
from app.core.errors import ServiceUnavailableError
from app.core.security import Principal


class RecordingConn:
    """Connection that records statements and the transaction steps."""

    def __init__(self, log: list[str]) -> None:
        """Record into the shared `log`."""
        self.log = log
        self.statements: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> None:
        """Record the statement with its parameters."""
        self.statements.append((str(stmt), params))
        self.log.append("execute")

    def begin(self) -> RecordingConn:
        """Return itself as the transaction."""
        return self

    async def __aenter__(self) -> RecordingConn:
        """Record entering the connection or transaction."""
        self.log.append("enter")
        return self

    async def __aexit__(self, exc_type: Any, *connect_args: Any) -> None:
        """Record leaving it, as a rollback when an error is raised."""
        self.log.append("rollback" if exc_type else "exit")


class RecordingEngine:
    """Engine that hands out one recording connection."""

    def __init__(self) -> None:
        """Create the shared log and the connection."""
        self.log: list[str] = []
        self.conn = RecordingConn(self.log)

    def connect(self) -> RecordingConn:
        """Return the recording connection."""
        return self.conn


def principal() -> Principal:
    """Return an authenticated caller with a fresh user id."""
    user_id = uuid.uuid4()
    return Principal(
        user_id=user_id, email="a@b.c", claims={"sub": str(user_id), "role": "authenticated"}
    )


async def test_apply_rls_context_sets_claims_then_role() -> None:
    """The session sets the caller's claims and timeout, then switches to `authenticated`."""
    conn = RecordingConn([])
    caller = principal()
    await apply_rls_context(conn, caller, 5000)  # type: ignore[arg-type]
    (claims_sql, params), (role_sql, _) = conn.statements
    assert "set_config('request.jwt.claims', :claims, true)" in claims_sql
    assert "set_config('request.jwt.claim.sub', :sub, true)" in claims_sql
    assert params is not None
    assert json.loads(params["claims"]) == caller.claims
    assert params["sub"] == str(caller.user_id)
    assert params["statement_timeout"] == "5000"
    assert role_sql == "set local role authenticated"


def _request(engine: Any) -> Any:
    """Return a request whose app state holds the engine and default settings."""
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(engine=engine, settings=Settings(_env_file=None)))  # type: ignore[call-arg]
    )


async def test_get_user_db_runs_in_transaction_as_user() -> None:
    """A user session runs in a transaction and sets the claims before yielding."""
    engine = RecordingEngine()
    session = get_user_db(_request(engine), principal())
    conn = await session.__anext__()
    assert conn is engine.conn
    assert engine.log == ["enter", "enter", "execute", "execute"]
    with pytest.raises(StopAsyncIteration):
        await session.__anext__()
    assert engine.log[-2:] == ["exit", "exit"]


async def test_get_user_db_rolls_back_on_error() -> None:
    """An error in the request rolls the transaction back."""
    engine = RecordingEngine()
    session = get_user_db(_request(engine), principal())
    await session.__anext__()
    with pytest.raises(RuntimeError):
        await session.athrow(RuntimeError("fail"))
    assert "rollback" in engine.log


async def test_get_service_db_does_not_switch_role() -> None:
    """The service session runs no RLS statements."""
    engine = RecordingEngine()
    session = get_service_db(_request(engine))
    await session.__anext__()
    assert engine.conn.statements == []
    await session.aclose()


async def test_missing_engine_is_503() -> None:
    """Without an engine a user session is 503."""
    session = get_user_db(_request(None), principal())
    with pytest.raises(ServiceUnavailableError):
        await session.__anext__()


def test_engine_is_pooler_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine uses asyncpg with a small pool and no prepared-statement caching."""
    captured: dict[str, Any] = {}

    def fake_create(url: Any, **kwargs: Any) -> str:
        """Record the engine's URL and options instead of creating it."""
        captured["url"] = url
        captured.update(kwargs)
        return "engine"

    monkeypatch.setattr(db_module, "create_async_engine", fake_create)
    configured = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql://postgres.ref:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres",
    )
    assert create_engine(configured) == "engine"
    assert captured["url"].drivername == "postgresql+asyncpg"
    assert captured["pool_size"] == 5
    assert captured["max_overflow"] == 5
    assert captured["pool_pre_ping"] is True
    connect_args = captured["connect_args"]
    assert connect_args["statement_cache_size"] == 0
    assert connect_args["prepared_statement_cache_size"] == 0
    name_func = connect_args["prepared_statement_name_func"]
    assert name_func() != name_func()


def test_no_engine_without_url() -> None:
    """Without a database URL there is no engine."""
    assert create_engine(Settings(_env_file=None)) is None  # type: ignore[call-arg]
