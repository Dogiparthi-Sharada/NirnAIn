"""RFC 7807 problem responses and the mapping of framework and database errors onto them.

Purpose: Every error the API returns is an `application/problem+json` document with a stable `type`,
the request id and no leaked internals (stack traces, SQL, echoed input).
How it fits: Routes and repositories raise the `AppError` subclasses; `app.main` registers the
handlers below; middleware uses `problem_response` as the last line of defence; layer: core.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_context

logger = logging.getLogger("app.errors")

PROBLEM_JSON = "application/problem+json"


def _type_uri(slug: str) -> str:
    """Return the problem `type` URI for an error slug."""
    return f"urn:problem-type:{slug}"


# --------------------------------------------------------------------------- app errors
class AppError(Exception):
    """Base for errors that render as a problem document."""

    status: int = 500
    slug: str = "internal-error"
    title: str = "Internal Server Error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Keep the human-readable detail, extra problem members and response headers."""
        super().__init__(detail or self.title)
        self.detail = detail
        self.extra = extra or {}
        self.headers = headers


class BadRequestError(AppError):
    """400: the request is malformed (for example an invalid pagination cursor)."""

    status, slug, title = 400, "bad-request", "Bad Request"


class UnauthorizedError(AppError):
    """401: the bearer token is missing, invalid or expired."""

    status, slug, title = 401, "unauthorized", "Unauthorized"

    def __init__(self, detail: str | None = "Missing or invalid access token") -> None:
        """Build a 401 that tells the client to send a bearer token."""
        super().__init__(detail, headers={"WWW-Authenticate": 'Bearer realm="api"'})


class ForbiddenError(AppError):
    """403: the caller may not do this (RLS or a role check said no)."""

    status, slug, title = 403, "forbidden", "Forbidden"


class NotFoundError(AppError):
    """404: the resource does not exist or is not visible to the caller."""

    status, slug, title = 404, "not-found", "Not Found"


class ConflictError(AppError):
    """409: the request clashes with the current state (duplicates, stale inputs, races)."""

    status, slug, title = 409, "conflict", "Conflict"


class ContentTooLargeError(AppError):
    """413: the upload or text is over the size limit."""

    status, slug, title = 413, "content-too-large", "Content Too Large"


class UnsupportedMediaTypeError(AppError):
    """415: the upload's file type is not accepted."""

    status, slug, title = 415, "unsupported-media-type", "Unsupported Media Type"


class UnprocessableError(AppError):
    """422: the request is well-formed but breaks a business rule or data constraint."""

    status, slug, title = 422, "unprocessable", "Unprocessable Content"


class ServiceUnavailableError(AppError):
    """503: a dependency (database, auth provider, AI) is not configured or not reachable."""

    status, slug, title = 503, "service-unavailable", "Service Unavailable"


# --------------------------------------------------------------------------- rendering
def _phrase(status: int) -> str:
    """Return the standard reason phrase for an HTTP status, or `Error` for unknown codes."""
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def problem_response(
    request: Request | None,
    *,
    status: int,
    title: str | None = None,
    detail: str | None = None,
    slug: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Render a problem+json response with the request path and request id filled in.

    `extra` members are merged into the body; `headers` are sent as-is.
    """
    phrase = _phrase(status)
    body: dict[str, Any] = {
        "type": _type_uri(slug) if slug else "about:blank",
        "title": title or phrase,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    if request is not None:
        body["instance"] = request.url.path
    request_context = get_request_context()
    if request_context is not None:
        body["requestId"] = request_context.request_id
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_JSON)


# --------------------------------------------------------------------------- DB errors
@dataclass(frozen=True, slots=True)
class PgErrorInfo:
    """The parts of a Postgres error the API maps on: SQLSTATE, message and constraint name."""

    sqlstate: str | None
    message: str | None
    constraint: str | None


def pg_error_info(exc: BaseException) -> PgErrorInfo:
    """Extract SQLSTATE / message from a SQLAlchemy-wrapped asyncpg error."""
    candidates: list[BaseException] = []
    orig = getattr(exc, "orig", None)
    for candidate in (
        orig,
        getattr(orig, "__cause__", None),
        getattr(orig, "__context__", None),
        exc,
    ):
        if isinstance(candidate, BaseException) and candidate not in candidates:
            candidates.append(candidate)

    sqlstate = message = constraint = None
    for candidate in candidates:
        sqlstate = (
            sqlstate or getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        )
        candidate_message = getattr(candidate, "message", None)
        if isinstance(candidate_message, str) and candidate_message and message is None:
            message = candidate_message
        constraint = constraint or getattr(candidate, "constraint_name", None)
    return PgErrorInfo(
        sqlstate if isinstance(sqlstate, str) else None,
        message,
        constraint if isinstance(constraint, str) else None,
    )


def map_db_error(exc: DBAPIError) -> AppError:  # noqa: PLR0911
    """Translate a Postgres error (by SQLSTATE) into the API error the client should see.

    42501 is an RLS or grant denial (403), P0001 a business rule raised by a trigger (422);
    unique/check/FK/not-null violations become 409/422 and connection problems 503.
    """
    info = pg_error_info(exc)
    code = info.sqlstate or ""
    extra = {"constraint": info.constraint} if info.constraint else None

    if code == "42501":
        return ForbiddenError("You do not have permission to perform this action")
    if code == "P0001":
        return UnprocessableError(info.message or "The request violates a business rule")
    if code == "23505":
        return ConflictError("A resource with the same unique value already exists", extra=extra)
    if code == "23514":
        return UnprocessableError("A value violates a data constraint", extra=extra)
    if code == "23503":
        return UnprocessableError("A referenced resource does not exist", extra=extra)
    if code == "23502":
        return UnprocessableError("A required value is missing", extra=extra)
    if code.startswith("22"):  # data exception: invalid input, string too long, ...
        return UnprocessableError("A value has an invalid format or length")
    if code in ("40001", "40P01"):
        return ConflictError("The request conflicted with a concurrent update; retry it")
    if code == "57014":
        return ServiceUnavailableError("The database query timed out")
    if isinstance(exc, (OperationalError, InterfaceError)) or code.startswith("08"):
        return ServiceUnavailableError("The database is temporarily unavailable")
    return AppError()


# --------------------------------------------------------------------------- handlers
async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render an `AppError` as its problem document; logs server-side failures."""
    exc = cast(AppError, exc)
    if exc.status >= 500:
        logger.error("request failed", extra={"error": exc.slug, "detail": exc.detail})
    return problem_response(
        request,
        status=exc.status,
        title=exc.title,
        detail=exc.detail,
        slug=exc.slug,
        extra=exc.extra,
        headers=exc.headers,
    )


async def _db_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map a database error to an `AppError`, log it by severity and render it."""
    exc = cast(DBAPIError, exc)
    mapped = map_db_error(exc)
    info = pg_error_info(exc)
    level = logging.ERROR if mapped.status >= 500 else logging.INFO
    logger.log(
        level,
        "database error",
        extra={"sqlstate": info.sqlstate, "mapped_status": mapped.status},
        exc_info=mapped.status >= 500 and type(mapped) is AppError,
    )
    return await _app_error_handler(request, mapped)


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render Starlette/FastAPI HTTP exceptions (404 route, 405 method) as problem documents."""
    exc = cast(StarletteHTTPException, exc)
    detail = exc.detail if isinstance(exc.detail, str) else None
    phrase = _phrase(exc.status_code)
    return problem_response(
        request,
        status=exc.status_code,
        detail=detail if detail != phrase else None,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render request validation errors as a 422 that lists location, message and type per error."""
    exc = cast(RequestValidationError, exc)
    # Never echo `input` back: it may contain secrets or large payloads.
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        title="Validation Failed",
        detail="The request is invalid",
        slug="validation-error",
        extra={"errors": errors},
    )


async def _pool_timeout_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer 503 when no pooled database connection became free in time."""
    logger.error("database pool exhausted")
    return await _app_error_handler(
        request, ServiceUnavailableError("The database is temporarily unavailable")
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log an unexpected exception and answer a bare 500 without internals."""
    logger.error("unhandled exception", exc_info=exc)
    return problem_response(request, status=500, slug="internal-error")


def register_exception_handlers(app: FastAPI) -> None:
    """Install every problem-document handler on the app."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(DBAPIError, _db_error_handler)
    app.add_exception_handler(PoolTimeoutError, _pool_timeout_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(ResponseValidationError, _unhandled_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
