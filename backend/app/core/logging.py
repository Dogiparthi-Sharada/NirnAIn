"""Structured JSON logging with per-request context (request id, user id).

Purpose: One JSON line per log record that always carries the request id (and the user id once
authenticated), so a support request can be traced through the logs.
How it fits: `app.main` calls `configure_logging`; the request-context middleware binds the context
and `app.core.security` records the user id; layer: core.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class RequestContext:
    """Mutable per-request state.

    Stored by reference in a ContextVar so that values set deep inside the
    request (e.g. the user id resolved by the auth dependency) are visible to
    the access-log middleware even if a framework layer copied the context.
    """

    request_id: str
    user_id: str | None = None


_request_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def get_request_context() -> RequestContext | None:
    """Return the context of the request being handled, if any."""
    return _request_context.get()


def bind_request_context(context: RequestContext) -> Token[RequestContext | None]:
    """Make `context` the current request context and return the token that undoes it."""
    return _request_context.set(context)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    """Restore the request context that was current before `bind_request_context`."""
    _request_context.reset(token)


def set_user_id(user_id: str) -> None:
    """Record the authenticated user's id on the current request context."""
    context = _request_context.get()
    if context is not None:
        context.user_id = user_id


# Attributes present on every LogRecord; anything else passed via `extra=` is emitted.
_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys() | {"message", "asctime", "taskName"}
)


class JsonFormatter(logging.Formatter):
    """Log formatter that writes each record as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialise a log record, its request context and any `extra=` fields as one JSON line."""
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        context = _request_context.get()
        if context is not None:
            payload["request_id"] = context.request_id
            if context.user_id:
                payload["user_id"] = context.user_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Send all logs, uvicorn's included, through the JSON formatter on stdout at `level`.

    uvicorn's own access log is disabled because the middleware writes a richer one.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Route uvicorn through our formatter; its own access log is replaced by ours.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers[:] = []
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = []
    access.propagate = False
    access.disabled = True
