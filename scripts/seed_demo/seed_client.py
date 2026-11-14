"""HTTP side of the demo seed: Supabase Auth sign-up / sign-in and the `/api/v1` client.

Purpose: Use only public APIs, the same ones the web app calls, with nothing outside the
Python standard library, so the seed runs unchanged on a laptop, in CI or against the
hosted demo.
How it fits: `seed_demo.py` builds an `AuthSession` (GoTrue at `{auth_url}/auth/v1`) and an
`ApiClient` (the FastAPI backend at `{api_url}/api/v1`); the flow modules call
`ApiClient.expect`, which raises `SeedError` on any unexpected answer so a failed step stops
the run with a clear message and a non-zero exit code; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

USER_AGENT = "nirnain-demo-seed/1.0"
ALLOWED_SCHEMES = ("http://", "https://")


class SeedError(RuntimeError):
    """A seeding step failed; the message says which step and what the API answered."""


@dataclass
class ApiResponse:
    """One HTTP answer: status, parsed JSON (or None) and the raw body."""

    status: int
    data: Any
    raw: bytes

    @property
    def detail(self) -> str:
        """The problem+json detail (or title) of an error answer, '' otherwise."""
        if isinstance(self.data, dict) and self.status >= 400:
            for key in ("detail", "title", "msg", "error_description", "message", "error"):
                if self.data.get(key):
                    return str(self.data[key])
        return self.raw[:200].decode(errors="replace") if self.status >= 400 else ""


def http_json(
    method: str,
    url: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = 60.0,
) -> ApiResponse:
    """Sends one JSON request and parses the answer; HTTP errors are returned, not raised."""
    if not url.startswith(ALLOWED_SCHEMES):
        raise SeedError(f"Only http(s) URLs are supported, not {url!r}")
    all_headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        all_headers["Content-Type"] = "application/json"
    # The scheme is checked above, so urllib cannot be pointed at file:// or other handlers.
    request = urllib.request.Request(url, data=payload, method=method, headers=all_headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as answer:  # noqa: S310
            status, raw, content_type = (
                answer.status,
                answer.read(),
                answer.headers.get("Content-Type", ""),
            )
    except urllib.error.HTTPError as error:
        status, raw, content_type = error.code, error.read(), error.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise SeedError(f"{method} {url} could not be reached: {error}") from error
    data = None
    if raw and "json" in (content_type or ""):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
    return ApiResponse(status, data, raw)


class AuthSession:
    """A signed-in Supabase Auth user: access token plus refresh, through the public gateway."""

    def __init__(self, auth_url: str, anon_key: str) -> None:
        """Remembers the Auth base URL and the anon key; no user is signed in yet."""
        self.auth_url = auth_url.rstrip("/") + "/auth/v1"
        self.anon_key = anon_key
        self.access_token = ""
        self.refresh_token = ""
        self.user_id = ""

    def _auth(self, path: str, body: dict[str, Any]) -> ApiResponse:
        """POSTs JSON to one Auth endpoint with the anon key."""
        return http_json("POST", self.auth_url + path, body, {"apikey": self.anon_key})

    def _keep(self, answer: ApiResponse) -> None:
        """Stores the tokens and user id of a successful sign-in or sign-up answer."""
        self.access_token = str(answer.data["access_token"])
        self.refresh_token = str(answer.data.get("refresh_token") or "")
        self.user_id = str((answer.data.get("user") or {}).get("id") or "")

    def sign_in(self, email: str, password: str) -> bool:
        """Password sign-in; False when the credentials are not accepted."""
        answer = self._auth("/token?grant_type=password", {"email": email, "password": password})
        if (
            answer.status == 200
            and isinstance(answer.data, dict)
            and answer.data.get("access_token")
        ):
            self._keep(answer)
            return True
        if answer.status in (400, 401, 422):
            return False
        raise SeedError(f"Sign-in for {email} failed: HTTP {answer.status} {answer.detail}")

    def sign_up_or_in(self, email: str, password: str, full_name: str) -> str:
        """Signs in, or signs up first (GoTrue `/signup` with the full name).

        Returns 'signed in' or 'signed up'.
        """
        if self.sign_in(email, password):
            return "signed in"
        answer = self._auth(
            "/signup", {"email": email, "password": password, "data": {"full_name": full_name}}
        )
        if answer.status in (200, 201) and isinstance(answer.data, dict):
            if answer.data.get("access_token"):
                self._keep(answer)
                return "signed up"
            if self.sign_in(email, password):
                return "signed up"
            # GoTrue answers an existing address like a new one when confirmations are on.
            raise SeedError(
                f"No session for {email}: either the address must be confirmed first "
                "(confirm it, or "
                "create the user with auto-confirm), or it already exists with another password."
            )
        if "already" in answer.detail.lower() or "exists" in answer.detail.lower():
            raise SeedError(
                f"{email} already exists and the password was not accepted. "
                "Set the right DEMO_PASSWORD."
            )
        raise SeedError(
            f"{email}: sign-in failed and sign-up was refused: HTTP {answer.status} {answer.detail}"
        )

    def refresh(self) -> bool:
        """New access token from the refresh token (long runs outlive a one-hour token)."""
        if not self.refresh_token:
            return False
        answer = self._auth(
            "/token?grant_type=refresh_token", {"refresh_token": self.refresh_token}
        )
        if (
            answer.status == 200
            and isinstance(answer.data, dict)
            and answer.data.get("access_token")
        ):
            self._keep(answer)
            return True
        return False


class ApiClient:
    """JSON calls to `{api}/api/v1` as the signed-in user (RLS applies on the server)."""

    def __init__(self, api_url: str, session: AuthSession) -> None:
        """Remembers the API base URL and the signed-in session whose token it sends."""
        self.base_url = api_url.rstrip("/") + "/api/v1"
        self.session = session

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        query: dict[str, str] | None = None,
        timeout_s: float = 90.0,
    ) -> ApiResponse:
        """One request; a 401 refreshes the token once and retries."""
        url = self.base_url + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        for attempt in range(2):
            answer = http_json(
                method,
                url,
                body,
                {"Authorization": f"Bearer {self.session.access_token}"},
                timeout_s,
            )
            if answer.status != 401 or attempt or not self.session.refresh():
                return answer
        return answer

    def expect(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        step: str,
        ok: tuple[int, ...] = (200, 201, 202, 204),
        query: dict[str, str] | None = None,
    ) -> Any:
        """Like `call`, but any status outside `ok` raises `SeedError` naming the step."""
        answer = self.call(method, path, body, query)
        if answer.status not in ok:
            raise SeedError(
                f"{step}: {method} {path} answered HTTP {answer.status} {answer.detail}"
            )
        return answer.data


def wait_until(
    fetch: Callable[[], Any],
    is_done: Callable[[Any], bool],
    *,
    step: str,
    timeout_s: float = 300.0,
    interval_s: float = 2.0,
) -> Any:
    """Polls `fetch` until `is_done`; raises `SeedError` after `timeout_s`."""
    deadline = time.monotonic() + timeout_s
    latest = fetch()
    while not is_done(latest):
        if time.monotonic() > deadline:
            raise SeedError(f"{step}: still not finished after {timeout_s:.0f} s")
        time.sleep(interval_s)
        latest = fetch()
    return latest


def wake_api(api_url: str, timeout_s: float = 180.0) -> None:
    """Waits for `{api}/health` (a free hosted API sleeps when idle and takes a minute to wake)."""
    url = api_url.rstrip("/") + "/health"

    def probe() -> int:
        """The health status, or 0 while the API cannot be reached."""
        try:
            return http_json("GET", url, timeout_s=30.0).status
        except SeedError:
            return 0

    wait_until(
        probe,
        lambda status: status == 200,
        step=f"Waking the API ({url})",
        timeout_s=timeout_s,
        interval_s=5.0,
    )
