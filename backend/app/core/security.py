"""Supabase Auth access-token verification.

Purpose: Only verified Supabase users may call the API. Tokens are checked against the project's
JWKS (ES256/RS256); legacy HS256 tokens only when `SUPABASE_JWT_SECRET` is set. Never logged.
How it fits: `app.main` builds the `TokenVerifier`; routes depend on `CurrentPrincipal`, whose
claims `app.core.db` publishes to Postgres for RLS; layer: core.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.core.config import Settings
from app.core.errors import ServiceUnavailableError, UnauthorizedError
from app.core.logging import set_user_id

logger = logging.getLogger("app.security")

ASYMMETRIC_ALGS = frozenset({"ES256", "RS256"})
AUDIENCE = "authenticated"
REQUIRED_ROLE = "authenticated"


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, derived from a verified access token."""

    user_id: uuid.UUID
    email: str | None
    claims: dict[str, Any] = field(repr=False)


class TokenVerifier:
    """Verifies Supabase access tokens and turns them into a `Principal`."""

    def __init__(
        self,
        *,
        issuer: str,
        jwks_client: PyJWKClient | None,
        hs256_secret: str | None = None,
        leeway_s: int = 10,
    ) -> None:
        """Keep the expected issuer, JWKS client, optional HS256 secret and clock leeway."""
        self._issuer = issuer
        self._jwks = jwks_client
        self._secret = hs256_secret
        self._leeway = leeway_s

    @classmethod
    def from_settings(cls, settings: Settings) -> TokenVerifier | None:
        """Build a verifier from settings, or return None when Supabase Auth is not configured."""
        if not settings.jwt_issuer or not settings.jwks_url:
            return None
        jwks = PyJWKClient(
            settings.jwks_url,
            cache_jwk_set=True,
            lifespan=settings.jwks_cache_lifespan_s,
            cache_keys=False,  # kid lookups go through the JWK Set cache; rotation refetches
            timeout=5,
        )
        secret = settings.supabase_jwt_secret
        return cls(
            issuer=settings.jwt_issuer,
            jwks_client=jwks,
            hs256_secret=secret.get_secret_value() if secret else None,
            leeway_s=settings.jwt_leeway_s,
        )

    async def verify(self, token: str) -> Principal:
        """Verify an access token and return the caller.

        Raises 401 for malformed, expired, wrongly signed or non-`authenticated` tokens and 503 when
        the signing keys cannot be fetched.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Malformed access token") from exc

        alg = header.get("alg")
        key: Any
        if alg in ASYMMETRIC_ALGS:
            if self._jwks is None:
                raise UnauthorizedError("Unsupported token algorithm")
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid:
                raise UnauthorizedError("Access token is missing a key id")
            key = await self._signing_key(self._jwks, kid)
        elif alg == "HS256" and self._secret:
            key = self._secret
        else:
            raise UnauthorizedError("Unsupported token algorithm")

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=AUDIENCE,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("Access token has expired") from exc
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Invalid access token") from exc

        if claims.get("role") != REQUIRED_ROLE:
            raise UnauthorizedError("Access token is not for an authenticated user")
        try:
            user_id = uuid.UUID(str(claims["sub"]))
        except ValueError as exc:
            raise UnauthorizedError("Invalid access token subject") from exc

        email = claims.get("email")
        return Principal(
            user_id=user_id, email=email if isinstance(email, str) else None, claims=claims
        )

    @staticmethod
    async def _signing_key(jwks: PyJWKClient, kid: str) -> Any:
        """Fetch the public key for `kid` from the JWKS without blocking the event loop."""
        try:
            # PyJWKClient uses blocking urllib; keep it off the event loop.
            # On an unknown kid it refetches the JWKS once (key rotation).
            signing_key = await asyncio.to_thread(jwks.get_signing_key, kid)
        except PyJWKClientConnectionError as exc:
            logger.error("jwks fetch failed", extra={"error": type(exc).__name__})
            raise ServiceUnavailableError("Authentication provider is unavailable") from exc
        except PyJWKClientError as exc:
            raise UnauthorizedError("Access token signing key is unknown") from exc
        return signing_key.key


_bearer = HTTPBearer(auto_error=False)


def get_token_verifier(request: Request) -> TokenVerifier:
    """Return the app's token verifier; raises 503 when authentication is not configured."""
    verifier: TokenVerifier | None = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise ServiceUnavailableError("Authentication is not configured")
    return verifier


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> Principal:
    """FastAPI dependency: authenticate the bearer token and record the user id for logging."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise UnauthorizedError()
    principal = await verifier.verify(credentials.credentials)
    set_user_id(str(principal.user_id))
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
