"""HTTP client, Supabase Auth sign-in and polling for the API end-to-end suite.

Purpose: Send JSON and multipart requests to the NirnAIn API and to Supabase Auth with nothing
but the Python standard library, so the suite runs on any Python 3.12 without installs.
How it fits: `run_api_suite.py` signs the accounts in through `AuthClient` and hands an
`ApiClient` to every scenario module; `wait_until` polls background jobs; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ALLOWED_SCHEMES = ("http://", "https://")
USER_AGENT = "nirnain-e2e-api/1.0"


class SuiteSetupError(RuntimeError):
    """The suite cannot start: a URL, the anon key or an account is wrong."""


@dataclass
class ApiResponse:
    """One HTTP answer: status, parsed JSON (or None), raw bytes and lower-cased headers."""

    status: int
    data: Any
    raw: bytes
    headers: dict[str, str]

    @property
    def detail(self) -> str:
        """The problem+json detail (or title) of an error answer, '' otherwise."""
        if isinstance(self.data, dict) and self.status >= 400:
            return str(self.data.get("detail") or self.data.get("title") or "")
        return ""


def send(
    method: str, url: str, payload: bytes | None, headers: dict[str, str], timeout_s: float
) -> ApiResponse:
    """Sends one request; HTTP error statuses come back as answers, not exceptions."""
    if not url.startswith(ALLOWED_SCHEMES):
        raise SuiteSetupError(f"Only http(s) URLs are supported, not {url!r}")
    all_headers = {"User-Agent": USER_AGENT, **headers}
    # The scheme is checked above, so urllib cannot be pointed at file:// or other handlers.
    request = urllib.request.Request(url, data=payload, method=method, headers=all_headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as answer:  # noqa: S310
            return parse_answer(answer.status, answer.read(), dict(answer.headers))
    except urllib.error.HTTPError as error:
        return parse_answer(error.code, error.read(), dict(error.headers))


def parse_answer(status: int, raw: bytes, headers: dict[str, str]) -> ApiResponse:
    """Builds an `ApiResponse`, parsing the body when the answer says it is JSON."""
    lowered = {name.lower(): value for name, value in headers.items()}
    data: Any = None
    if raw and "json" in lowered.get("content-type", ""):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
    return ApiResponse(status, data, raw, lowered)


def multipart_file(upload: tuple[str, bytes, str]) -> tuple[bytes, str]:
    """Encodes (file name, bytes, MIME type) as a multipart `file` field; returns (body, type)."""
    boundary = f"e2e-{uuid.uuid4().hex}"
    file_name, content, mime_type = upload
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode()
    content_type = f"multipart/form-data; boundary={boundary}"
    return head + content + f"\r\n--{boundary}--\r\n".encode(), content_type


class ApiClient:
    """JSON and multipart calls to `{api_url}/api/v1` with the caller's bearer token."""

    def __init__(self, api_url: str, token: str | None) -> None:
        """Remembers the API base URL and the access token (None for anonymous calls)."""
        self.base_url = api_url.rstrip("/")
        self.token = token

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        upload: tuple[str, bytes, str] | None = None,
        query: dict[str, str] | None = None,
        timeout_s: float = 60.0,
    ) -> ApiResponse:
        """Sends one request; `upload` is (file name, bytes, MIME type) for a multipart file."""
        url = f"{self.base_url}/api/v1{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json, */*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload: bytes | None = None
        if upload is not None:
            payload, headers["Content-Type"] = multipart_file(upload)
        elif body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        return send(method, url, payload, headers, timeout_s)


class AuthClient:
    """Supabase Auth (GoTrue) at `{auth_url}/auth/v1`, called with the public anon key."""

    def __init__(self, auth_url: str, anon_key: str) -> None:
        """Remembers the Auth base URL and the anon (publishable) key."""
        self.base_url = auth_url.rstrip("/") + "/auth/v1"
        self.anon_key = anon_key

    def _post(self, path: str, body: dict[str, Any]) -> ApiResponse:
        """POSTs JSON to one Auth endpoint with the anon key."""
        headers = {"apikey": self.anon_key, "Content-Type": "application/json"}
        return send("POST", self.base_url + path, json.dumps(body).encode(), headers, 30.0)

    def sign_in(self, email: str, password: str) -> str:
        """Password sign-in; returns the access token or raises `SuiteSetupError`."""
        answer = self._post("/token?grant_type=password", {"email": email, "password": password})
        token = answer.data.get("access_token") if isinstance(answer.data, dict) else None
        if answer.status != 200 or not token:
            reason = answer.raw[:160].decode(errors="replace")
            raise SuiteSetupError(f"Sign-in as {email} failed: HTTP {answer.status} {reason}")
        return str(token)

    def sign_up(self, email: str, password: str, full_name: str) -> None:
        """Creates an account (its sign-up trigger also creates its own workspace)."""
        body = {"email": email, "password": password, "data": {"full_name": full_name}}
        answer = self._post("/signup", body)
        if answer.status not in {200, 201}:
            reason = answer.raw[:160].decode(errors="replace")
            raise SuiteSetupError(f"Sign-up of {email} failed: HTTP {answer.status} {reason}")


def throwaway_password() -> str:
    """A random password that meets GoTrue's rules (lower case, upper case and a digit)."""
    return f"E2e-{secrets.token_urlsafe(12)}-Aa1"


def wait_until(
    fetch: Callable[[], ApiResponse],
    is_done: Callable[[ApiResponse], bool],
    timeout_s: float = 150.0,
    interval_s: float = 1.5,
) -> ApiResponse:
    """Polls `fetch` until `is_done` holds (or the timeout passes); returns the last answer."""
    deadline = time.monotonic() + timeout_s
    latest = fetch()
    while not is_done(latest) and time.monotonic() < deadline:
        time.sleep(interval_s)
        latest = fetch()
    return latest
