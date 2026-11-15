"""Tests of the Supabase JWT verification.

Purpose: Only valid tokens for authenticated users may reach the API: right key, audience, issuer,
role, subject and expiry; key rotation must work and outages must be 503.
How it fits: Exercises app/core/security.py `TokenVerifier` with the keys and fake JWKS client of
tests/support/tokens.py; layer: tests.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from app.core.errors import ServiceUnavailableError, UnauthorizedError
from app.core.security import TokenVerifier
from tests.support.tokens import ISSUER, FakeJWKClient, KeyPair, TokenFactory


async def test_valid_es256_token(verifier: TokenVerifier, make_token: TokenFactory) -> None:
    """A valid ES256 token gives the caller's id, email and claims."""
    user_id = uuid.uuid4()
    principal = await verifier.verify(make_token(user_id))
    assert principal.user_id == user_id
    assert principal.email == "jane@example.com"
    assert principal.claims["role"] == "authenticated"


async def test_jwks_is_cached(
    verifier: TokenVerifier, make_token: TokenFactory, jwks_client: FakeJWKClient
) -> None:
    """The JWKS is fetched once for many tokens."""
    for _ in range(3):
        await verifier.verify(make_token())
    assert jwks_client.fetches == 1


async def test_key_rotation_refetches_jwks(
    verifier: TokenVerifier, make_token: TokenFactory, jwks_client: FakeJWKClient
) -> None:
    """A token signed with a new key refetches the JWKS once."""
    await verifier.verify(make_token())
    new_key = KeyPair("key-2")
    jwks_client.keys.append(new_key)
    principal = await verifier.verify(make_token(key=new_key))
    assert principal.user_id
    assert jwks_client.fetches == 2


async def test_unknown_kid_rejected(verifier: TokenVerifier, make_token: TokenFactory) -> None:
    """A token with an unknown key id is refused."""
    with pytest.raises(UnauthorizedError, match="signing key"):
        await verifier.verify(make_token(key=KeyPair("rogue")))


async def test_wrong_signature_rejected(verifier: TokenVerifier, make_token: TokenFactory) -> None:
    """A token signed by another key with the same id is refused."""
    impostor = KeyPair("key-1")  # same kid, different key
    with pytest.raises(UnauthorizedError, match="Invalid"):
        await verifier.verify(make_token(key=impostor))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"exp": int(time.time()) - 3600}, "expired"),
        ({"aud": "anon"}, "Invalid"),
        ({"iss": "https://evil.example.com/auth/v1"}, "Invalid"),
        ({"role": "anon"}, "not for an authenticated user"),
        ({"role": "service_role"}, "not for an authenticated user"),
        ({"sub": "not-a-uuid"}, "subject"),
        ({"exp": None}, "Invalid"),
    ],
)
async def test_claim_validation(
    verifier: TokenVerifier, make_token: TokenFactory, overrides: dict[str, object], message: str
) -> None:
    """Expired, wrong audience, issuer, role or subject, and missing expiry are refused."""
    with pytest.raises(UnauthorizedError, match=message):
        await verifier.verify(make_token(**overrides))


async def test_hs256_fallback_with_secret(
    verifier: TokenVerifier, make_token: TokenFactory
) -> None:
    """HS256 tokens are accepted when the legacy secret is set."""
    user_id = uuid.uuid4()
    principal = await verifier.verify(make_token(user_id, alg="HS256"))
    assert principal.user_id == user_id


async def test_hs256_rejected_without_secret(
    jwks_client: FakeJWKClient, make_token: TokenFactory
) -> None:
    """HS256 tokens are refused without the legacy secret."""
    token_verifier = TokenVerifier(issuer=ISSUER, jwks_client=jwks_client, hs256_secret=None)
    with pytest.raises(UnauthorizedError, match="algorithm"):
        await token_verifier.verify(make_token(alg="HS256"))


async def test_alg_none_rejected(verifier: TokenVerifier) -> None:
    """Unsigned tokens are refused."""
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "iss": ISSUER, "role": "authenticated"},
        key=None,
        algorithm="none",
    )
    with pytest.raises(UnauthorizedError, match="algorithm"):
        await verifier.verify(token)


async def test_missing_kid_rejected(verifier: TokenVerifier, signing_key: KeyPair) -> None:
    """A token without a key id is refused."""
    token = jwt.encode({"sub": str(uuid.uuid4())}, signing_key.private, algorithm="ES256")
    with pytest.raises(UnauthorizedError, match="key id"):
        await verifier.verify(token)


async def test_garbage_token_rejected(verifier: TokenVerifier) -> None:
    """A string that isn't a JWT is refused as malformed."""
    with pytest.raises(UnauthorizedError, match="Malformed"):
        await verifier.verify("not-a-jwt")


async def test_jwks_outage_is_503(
    verifier: TokenVerifier, make_token: TokenFactory, jwks_client: FakeJWKClient
) -> None:
    """A JWKS outage is 503, not 401."""
    jwks_client.fail = True
    with pytest.raises(ServiceUnavailableError):
        await verifier.verify(make_token())


def test_from_settings_requires_supabase_url() -> None:
    """No verifier is built without the Supabase URL."""
    from app.core.config import Settings

    assert TokenVerifier.from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    token_verifier = TokenVerifier.from_settings(
        Settings(_env_file=None, supabase_url="https://x.supabase.co/")  # type: ignore[call-arg]
    )
    assert token_verifier is not None
