"""Shared pytest fixtures: signing keys and tokens, settings, the in-memory store and the app.

Purpose: Every endpoint test runs the real FastAPI app with real JWT verification, while the
repositories are swapped for in-memory fakes that keep the RLS rules.
How it fits: pytest loads it for every test; package conftests (tests/*/conftest.py) add their
area's fakes on top; helpers live in tests/support; layer: tests.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import Settings
from app.core.security import CurrentPrincipal, TokenVerifier
from app.main import create_app
from tests.support.store import FakeNotifications, FakeOrgs, FakeProfiles, FakeProjects, Store
from tests.support.tokens import (
    HS_SECRET,
    ISSUER,
    SUPABASE_URL,
    FakeJWKClient,
    KeyPair,
    TokenFactory,
)


@pytest.fixture
def signing_key() -> KeyPair:
    """Provide the ES256 key the fake JWKS publishes."""
    return KeyPair("key-1")


@pytest.fixture
def jwks_client(signing_key: KeyPair) -> FakeJWKClient:
    """Provide a JWKS client serving the signing key from memory."""
    return FakeJWKClient([signing_key])


@pytest.fixture
def verifier(jwks_client: FakeJWKClient) -> TokenVerifier:
    """Provide a token verifier for the test issuer (JWKS plus the legacy HS256 secret)."""
    return TokenVerifier(issuer=ISSUER, jwks_client=jwks_client, hs256_secret=HS_SECRET)


@pytest.fixture
def make_token(signing_key: KeyPair) -> TokenFactory:
    """Provide a factory of signed access tokens for a subject, with claim overrides."""

    def _make(
        sub: uuid.UUID | str | None = None,
        *,
        key: KeyPair | None = None,
        alg: str = "ES256",
        **overrides: Any,
    ) -> str:
        """Return a signed token for `sub`; a None override drops a claim; `alg`: ES256/HS256."""
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": str(sub or uuid.uuid4()),
            "aud": "authenticated",
            "iss": ISSUER,
            "role": "authenticated",
            "email": "jane@example.com",
            "iat": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        claims = {name: value for name, value in claims.items() if value is not None}
        if alg == "HS256":
            return jwt.encode(claims, HS_SECRET, algorithm="HS256")
        chosen_key = key or signing_key
        return jwt.encode(
            claims, chosen_key.private, algorithm="ES256", headers={"kid": chosen_key.kid}
        )

    return _make


# --------------------------------------------------------------------------- settings / app
@pytest.fixture
def settings() -> Settings:
    """Provide test settings (no `.env`, the test Supabase URL, one CORS origin)."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        supabase_url=SUPABASE_URL,
        cors_origins=["http://localhost:5173"],
        log_level="WARNING",
    )


# --------------------------------------------------------------------------- app/client
@pytest.fixture
def store() -> Store:
    """Provide a fresh in-memory store."""
    return Store()


@pytest.fixture
def app(settings: Settings, verifier: TokenVerifier, store: Store) -> FastAPI:
    """Provide the real app with the core repositories replaced by fakes over the store."""
    application = create_app(settings)
    application.state.token_verifier = verifier

    def profiles() -> FakeProfiles:
        """Return the fake profile repository."""
        return FakeProfiles(store)

    def orgs(principal: CurrentPrincipal) -> FakeOrgs:
        """Return the fake organization repository acting as the caller."""
        return FakeOrgs(store, principal.user_id)

    def projects(principal: CurrentPrincipal) -> FakeProjects:
        """Return the fake project repository acting as the caller."""
        return FakeProjects(store, principal.user_id)

    def notifications(principal: CurrentPrincipal) -> FakeNotifications:
        """Return the fake notification repository acting as the caller."""
        return FakeNotifications(store, principal.user_id)

    application.dependency_overrides[deps.get_profile_repo] = profiles
    application.dependency_overrides[deps.get_org_repo] = orgs
    application.dependency_overrides[deps.get_project_repo] = projects
    application.dependency_overrides[deps.get_notification_repo] = notifications
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Provide a test client that turns server errors into 500 responses."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def auth(make_token: TokenFactory) -> Callable[[uuid.UUID], dict[str, str]]:
    """Provide a function that builds a user's Authorization header."""

    def _headers(user_id: uuid.UUID) -> dict[str, str]:
        """Return the bearer header with a fresh token for the user."""
        return {"Authorization": f"Bearer {make_token(user_id)}"}

    return _headers
