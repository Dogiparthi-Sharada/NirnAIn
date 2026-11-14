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
    return KeyPair("key-1")


@pytest.fixture
def jwks_client(signing_key: KeyPair) -> FakeJWKClient:
    return FakeJWKClient([signing_key])


@pytest.fixture
def verifier(jwks_client: FakeJWKClient) -> TokenVerifier:
    return TokenVerifier(issuer=ISSUER, jwks_client=jwks_client, hs256_secret=HS_SECRET)


@pytest.fixture
def make_token(signing_key: KeyPair) -> TokenFactory:
    def _make(
        sub: uuid.UUID | str | None = None,
        *,
        key: KeyPair | None = None,
        alg: str = "ES256",
        **overrides: Any,
    ) -> str:
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
        claims = {k: v for k, v in claims.items() if v is not None}
        if alg == "HS256":
            return jwt.encode(claims, HS_SECRET, algorithm="HS256")
        k = key or signing_key
        return jwt.encode(claims, k.private, algorithm="ES256", headers={"kid": k.kid})

    return _make


# --------------------------------------------------------------------------- settings / app
@pytest.fixture
def settings() -> Settings:
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
    return Store()


@pytest.fixture
def app(settings: Settings, verifier: TokenVerifier, store: Store) -> FastAPI:
    application = create_app(settings)
    application.state.token_verifier = verifier

    def profiles() -> FakeProfiles:
        return FakeProfiles(store)

    def orgs(principal: CurrentPrincipal) -> FakeOrgs:
        return FakeOrgs(store, principal.user_id)

    def projects(principal: CurrentPrincipal) -> FakeProjects:
        return FakeProjects(store, principal.user_id)

    def notifications(principal: CurrentPrincipal) -> FakeNotifications:
        return FakeNotifications(store, principal.user_id)

    application.dependency_overrides[deps.get_profile_repo] = profiles
    application.dependency_overrides[deps.get_org_repo] = orgs
    application.dependency_overrides[deps.get_project_repo] = projects
    application.dependency_overrides[deps.get_notification_repo] = notifications
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def auth(make_token: TokenFactory) -> Callable[[uuid.UUID], dict[str, str]]:
    def _headers(user_id: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(user_id)}"}

    return _headers
