"""Tests of the app's cross-cutting behaviour: health, request ids, headers, CORS and errors.

Purpose: Every response must carry a request id and security headers, errors must be problem+json
without leaking internals, and production must hide the docs and send HSTS.
How it fits: Exercises app/main.py and the middleware and logging of app/core; layer: tests.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health_without_db(client: TestClient) -> None:
    """Health answers ok with the version even without a database."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "dev"}


def test_health_reports_git_sha(settings: Settings) -> None:
    """Health reports the deployed git commit as the version."""
    app = create_app(settings.model_copy(update={"railway_git_commit_sha": "deadbeef"}))
    with TestClient(app) as test_client:
        assert test_client.get("/health").json()["version"] == "deadbeef"


def test_ready_is_503_without_db(client: TestClient) -> None:
    """Readiness is 503 problem+json without a database."""
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"


def test_module_level_app_imports() -> None:
    """The module-level `app` that uvicorn imports is a FastAPI app."""
    from app.main import app

    assert isinstance(app, FastAPI)


def test_request_id_generated_and_echoed(client: TestClient) -> None:
    """A request id is generated when missing and echoed when given."""
    response = client.get("/health")
    assert len(response.headers["x-request-id"]) == 32
    response = client.get("/health", headers={"X-Request-ID": "abc-123-def-456"})
    assert response.headers["x-request-id"] == "abc-123-def-456"


def test_malformed_request_id_replaced(client: TestClient) -> None:
    """A malformed request id is replaced by a generated one."""
    for bad in ("short", "has spaces in it", "inject;newline-0001", "x" * 200):
        response = client.get("/health", headers={"X-Request-ID": bad})
        assert response.headers["x-request-id"] != bad
        assert len(response.headers["x-request-id"]) == 32


def test_security_headers(client: TestClient) -> None:
    """Responses carry the security headers; HSTS only in production."""
    headers = client.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["content-security-policy"].startswith("default-src 'none'")
    assert "strict-transport-security" not in headers


def test_hsts_in_production(settings: Settings) -> None:
    """Production sends HSTS and hides the API docs and schema."""
    production = settings.model_copy(
        update={"environment": "production", "database_url": None, "enable_docs": False}
    )
    with TestClient(create_app(production)) as test_client:
        assert "strict-transport-security" in test_client.get("/health").headers
        assert test_client.get("/api/docs").status_code == 404
        assert test_client.get("/api/openapi.json").status_code == 404


def test_docs_available_outside_production(client: TestClient) -> None:
    """Outside production the docs load (with their CDN allowed) and list the API."""
    response = client.get("/api/docs")
    assert response.status_code == 200
    assert "cdn.jsdelivr.net" in response.headers["content-security-policy"]
    spec = client.get("/api/openapi.json").json()
    assert "/api/v1/orgs/{orgId}/projects" in spec["paths"]


def test_cors_allowed_origin(client: TestClient) -> None:
    """A preflight from an allowed origin is allowed with credentials."""
    response = client.options(
        "/api/v1/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_disallowed_origin(client: TestClient) -> None:
    """Another origin gets no CORS header."""
    response = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


def test_unknown_route_is_problem_json(client: TestClient) -> None:
    """An unknown route is 404 problem+json with the path and request id."""
    response = client.get("/api/v1/nope", headers={"X-Request-ID": "req-00000001"})
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/nope"
    assert body["requestId"] == "req-00000001"


def test_method_not_allowed_is_problem_json(client: TestClient) -> None:
    """A wrong method is 405 problem+json."""
    response = client.put("/health")
    assert response.status_code == 405
    assert response.headers["content-type"] == "application/problem+json"


def test_unhandled_error_is_500_without_leak(app: FastAPI) -> None:
    """An unhandled error is a generic 500 problem+json without the error or traceback."""

    @app.get("/boom")
    async def boom() -> None:
        """Fail with a secret in the message."""
        raise RuntimeError("secret internals: password=hunter2")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
    assert response.json()["title"] == "Internal Server Error"
    assert "x-request-id" in response.headers


def test_access_log_line(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    """Each request logs one access line with method, path, status and duration."""
    with caplog.at_level(logging.INFO, logger="app.access"):
        client.get("/health", headers={"X-Request-ID": "log-test-0001"})
    records = [log_record for log_record in caplog.records if log_record.name == "app.access"]
    assert records
    access_record = records[-1]
    assert access_record.__dict__["method"] == "GET"
    assert access_record.__dict__["path"] == "/health"
    assert access_record.__dict__["status"] == 200
    assert isinstance(access_record.__dict__["duration_ms"], float)


def test_json_formatter_includes_context() -> None:
    """JSON log lines carry the request id, user id and extra fields."""
    from app.core.logging import (
        JsonFormatter,
        RequestContext,
        bind_request_context,
        reset_request_context,
        set_user_id,
    )

    token = bind_request_context(RequestContext(request_id="rid-1"))
    try:
        set_user_id("user-1")
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", (), None)
        record.status = 200
        formatted = json.loads(JsonFormatter().format(record))
    finally:
        reset_request_context(token)
    assert formatted["msg"] == "hello"
    assert formatted["request_id"] == "rid-1"
    assert formatted["user_id"] == "user-1"
    assert formatted["status"] == 200
