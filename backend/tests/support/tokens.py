"""Signed test tokens: the issuer, signing keys, an in-memory JWKS and the Auth header type.

Purpose: Let tests exercise real JWT verification (ES256 against a JWKS, legacy HS256) without
calling Supabase.
How it fits: tests/conftest.py builds the verifier and token fixtures from these; test modules
annotate the `auth` fixture with `Auth`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from jwt import PyJWKClient
from jwt.algorithms import ECAlgorithm

SUPABASE_URL = "https://abcdefghijkl.supabase.co"


ISSUER = f"{SUPABASE_URL}/auth/v1"


HS_SECRET = "super-secret-jwt-token-with-at-least-32-characters"


# --------------------------------------------------------------------------- JWT
class KeyPair:
    """An ES256 signing key with its key id, published through the fake JWKS."""

    def __init__(self, kid: str) -> None:
        """Generate a P-256 private key for the given key id."""
        self.kid = kid
        self.private = ec.generate_private_key(ec.SECP256R1())

    def jwk(self) -> dict[str, Any]:
        """Return the public key as a JWK, the way Supabase publishes it."""
        data: dict[str, Any] = json.loads(ECAlgorithm.to_jwk(self.private.public_key()))
        data.update({"kid": self.kid, "alg": "ES256", "use": "sig"})
        return data


class FakeJWKClient(PyJWKClient):
    """PyJWKClient whose network fetch is replaced by an in-memory JWKS."""

    def __init__(self, keys: list[KeyPair]) -> None:
        """Serve the given keys; `fail` makes fetches raise like an unreachable provider."""
        super().__init__("https://jwks.invalid/.well-known/jwks.json", cooldown_duration=0)
        self.keys = keys
        self.fetches = 0
        self.fail = False

    def fetch_data(self) -> Any:
        """Return the in-memory JWKS (counting fetches), or raise when `fail` is set."""
        from jwt.exceptions import PyJWKClientConnectionError

        if self.fail:
            raise PyJWKClientConnectionError("boom")
        self.fetches += 1
        return {"keys": [key.jwk() for key in self.keys]}


TokenFactory = Callable[..., str]


# Builds the Authorization header of a request made as the given user.
Auth = Callable[[uuid.UUID], dict[str, str]]
