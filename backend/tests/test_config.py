"""Tests of the settings read from the environment.

Purpose: Production must refuse to start half-configured; origins, the database URL and the AI key
must be read the way the deployment gives them.
How it fits: Exercises app/core/config.py `Settings`, without a .env file; layer: tests.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make_settings(**kwargs: object) -> Settings:
    """Return settings from the given values and environment, ignoring any .env file."""
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_production_requires_database_and_supabase() -> None:
    """Production without the database and Supabase URLs refuses to start."""
    with pytest.raises(ValidationError, match="DATABASE_URL, SUPABASE_URL"):
        make_settings(environment="production")


def test_production_ok_when_configured() -> None:
    """Configured production starts with the docs off unless enabled."""
    configured = make_settings(
        environment="production",
        database_url="postgresql://u:p@h:5432/postgres",
        supabase_url="https://x.supabase.co",
    )
    assert configured.is_production
    assert configured.docs_enabled is False
    assert make_settings(
        environment="production", enable_docs=True, database_url="x://", supabase_url="y"
    )


def test_cors_origins_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    """CORS origins are read comma-separated and without trailing slashes."""
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com, http://localhost:5173/")
    assert make_settings().cors_origins == ["https://app.example.com", "http://localhost:5173"]


def test_cors_wildcard_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wildcard CORS origin is refused."""
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(ValidationError, match="not allowed"):
        make_settings()


def test_database_url_normalised_for_asyncpg() -> None:
    """The database URL is switched to asyncpg and its sslmode moved to the SSL setting."""
    configured = make_settings(
        database_url=(
            "postgresql://postgres.ref:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
            "?sslmode=require"
        )
    )
    resolved = configured.sqlalchemy_database_url()
    assert resolved is not None
    url, ssl = resolved
    assert url.drivername == "postgresql+asyncpg"
    assert url.username == "postgres.ref"
    assert "sslmode" not in url.query
    assert ssl == "require"


def test_derived_urls_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """The JWT issuer, JWKS URL and version are derived from the environment."""
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    configured = make_settings(supabase_url="https://x.supabase.co/")
    assert configured.jwt_issuer == "https://x.supabase.co/auth/v1"
    assert configured.jwks_url == "https://x.supabase.co/auth/v1/.well-known/jwks.json"
    assert configured.version == "abc123"
    assert make_settings().sqlalchemy_database_url() is None


def test_blank_anthropic_key_means_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank Anthropic key counts as no key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    assert make_settings().anthropic_api_key is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    key = make_settings().anthropic_api_key
    assert key is not None
    assert key.get_secret_value() == "sk-test"
