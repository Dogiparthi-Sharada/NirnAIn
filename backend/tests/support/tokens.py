"""Moved test support."""

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
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private = ec.generate_private_key(ec.SECP256R1())

    def jwk(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(ECAlgorithm.to_jwk(self.private.public_key()))
        data.update({"kid": self.kid, "alg": "ES256", "use": "sig"})
        return data


class FakeJWKClient(PyJWKClient):
    """PyJWKClient whose network fetch is replaced by an in-memory JWKS."""

    def __init__(self, keys: list[KeyPair]) -> None:
        super().__init__("https://jwks.invalid/.well-known/jwks.json", cooldown_duration=0)
        self.keys = keys
        self.fetches = 0
        self.fail = False

    def fetch_data(self) -> Any:
        from jwt.exceptions import PyJWKClientConnectionError

        if self.fail:
            raise PyJWKClientConnectionError("boom")
        self.fetches += 1
        return {"keys": [k.jwk() for k in self.keys]}


TokenFactory = Callable[..., str]


# Builds the Authorization header of a request made as the given user.
Auth = Callable[[uuid.UUID], dict[str, str]]
