"""Shared setup of the browser regression tests: settings, accounts, test projects and the browser.

Purpose: Each browser test needs the same things before it can check a screen: settings from
NIRNAIN_* environment variables, a signed-in throwaway account, a project built through the
public API (rules intake and template WBS, so no Claude tokens), a Chromium page signed in to
the web app, and a PASS/FAIL log. Keeping them here keeps each test about its own scenario.
How it fits: Imported by the `test_*.py` scripts in this folder; reuses the standard-library
HTTP client of the API suite (`tests/e2e/api/suite_client.py`) and drives the web app with
Playwright for Python; layer: tests.
Created: 2026-11-15 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# The API suite's client is plain modules, not a package: put its folder on the import path.
API_SUITE_DIR = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(API_SUITE_DIR))

from playwright.sync_api import Page, Route, sync_playwright  # noqa: E402
from suite_client import (  # noqa: E402
    ApiClient,
    AuthClient,
    SuiteSetupError,
    throwaway_password,
    wait_until,
)
from suite_fixtures import MEETING_NOTES, constraint_body  # noqa: E402

DEFAULT_WEB_URL = "http://127.0.0.1:58940"
DEFAULT_API_URL = "http://127.0.0.1:58001"
DEFAULT_AUTH_URL = "http://localhost:54321"
DEFAULT_EMAIL_DOMAIN = "nirnain.local"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parent / "reports"
JOB_DONE = {"succeeded", "failed", "cancelled"}
SCREEN_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class BrowserSettings:
    """Where the web app, the API and Auth run, and which account signs in."""

    web_url: str
    api_url: str
    auth_url: str
    anon_key: str
    email: str | None
    password: str | None
    fresh_email_domain: str
    headed: bool
    report_dir: Path


@dataclass(frozen=True)
class Account:
    """A signed-in account: its credentials, an API client with its token and its workspace."""

    email: str
    password: str
    client: ApiClient
    org_id: str


@dataclass
class CheckLog:
    """PASS/FAIL lines of one test run; the exit code is 1 when any check failed."""

    title: str
    lines: list[str] = field(default_factory=list)
    failures: int = 0

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        """Records and prints one check; returns whether it passed."""
        verdict = "PASS" if passed else "FAIL"
        self.failures += 0 if passed else 1
        line = f"{verdict} {name}" + (f": {detail}" if detail else "")
        self.lines.append(line)
        print(line, flush=True)
        return passed

    def note(self, text: str) -> None:
        """Prints an observation that is not a verdict."""
        print(f"  · {text}", flush=True)

    def exit_code(self) -> int:
        """0 when every check passed, else 1, after printing the totals."""
        passed = len(self.lines) - self.failures
        print(f"{self.title}: PASS {passed} · FAIL {self.failures}", flush=True)
        return 1 if self.failures else 0


def load_settings() -> BrowserSettings:
    """Reads the NIRNAIN_* environment variables; the anon key is the only required one."""
    env = os.environ.get
    anon_key = env("NIRNAIN_ANON_KEY")
    if not anon_key:
        raise SuiteSetupError("Set NIRNAIN_ANON_KEY (the anon / publishable key of the stack)")
    password = env("NIRNAIN_QA_PASSWORD") or None
    return BrowserSettings(
        web_url=env("NIRNAIN_WEB_URL", DEFAULT_WEB_URL).rstrip("/"),
        api_url=env("NIRNAIN_API_URL", DEFAULT_API_URL).rstrip("/"),
        auth_url=env("NIRNAIN_AUTH_URL", DEFAULT_AUTH_URL).rstrip("/"),
        anon_key=anon_key,
        email=env("NIRNAIN_QA_EMAIL") if password else None,
        password=password,
        fresh_email_domain=env("NIRNAIN_FRESH_EMAIL_DOMAIN", DEFAULT_EMAIL_DOMAIN),
        headed=env("NIRNAIN_BROWSER_HEADED") == "1",
        report_dir=Path(env("NIRNAIN_E2E_REPORT_DIR", str(DEFAULT_REPORT_DIR))),
    )


def sign_in_account(settings: BrowserSettings) -> Account:
    """Signs in the configured account, or signs up a throwaway one with its own workspace."""
    auth = AuthClient(settings.auth_url, settings.anon_key)
    email, password = settings.email, settings.password
    if not email or not password:
        stamp = f"{datetime.now():%Y%m%d%H%M%S}.{os.getpid()}"
        email = f"qa.browser.{stamp}@{settings.fresh_email_domain}"
        password = throwaway_password()
        auth.sign_up(email, password, "QA Browser")
    client = ApiClient(settings.api_url, auth.sign_in(email, password))
    me = client.call("GET", "/me")
    workspaces = (me.data or {}).get("organizations") or [] if me.status == 200 else []
    if not workspaces:
        raise SuiteSetupError(f"GET /me answered HTTP {me.status} without a workspace")
    return Account(email, password, client, str(workspaces[0]["id"]))


# ───────────────────────────── Projects through the API ─────────────────────────────


def create_project(account: Account, name: str) -> str:
    """Creates a planning project that starts next week; returns its id."""
    start = date.today() + timedelta(days=7)
    body = {
        "name": name,
        "startDate": start.isoformat(),
        "targetEndDate": (start + timedelta(days=240)).isoformat(),
        "budgetCents": 50_000_000,
        "status": "planning",
        "currentStep": "intake",
    }
    created = account.client.call("POST", f"/orgs/{account.org_id}/projects", body)
    if created.status != 201:
        raise SuiteSetupError(f"Creating the project failed: HTTP {created.status}")
    return str(created.data["id"])


def run_rules_intake(account: Account, project_id: str) -> int:
    """Runs the intake on the API suite's meeting notes (rules, no key); returns the item count."""
    base = f"/projects/{project_id}"
    started = account.client.call("POST", f"{base}/intake/runs", {"text": MEETING_NOTES})
    if started.status not in {201, 202}:
        raise SuiteSetupError(f"Starting the intake failed: HTTP {started.status}")
    run = wait_until(
        lambda: account.client.call("GET", f"{base}/intake/runs/{started.data['id']}"),
        lambda answer: (answer.data or {}).get("status") in JOB_DONE,
    ).data
    if (run or {}).get("status") != "succeeded":
        raise SuiteSetupError(f"The intake did not succeed: {run}")
    return len(scope_items(account, project_id))


def scope_items(account: Account, project_id: str) -> list[dict[str, Any]]:
    """The project's scope items as the API lists them."""
    return account.client.call("GET", f"/projects/{project_id}/scope-items").data or []


def accept_all_scope(account: Account, project_id: str) -> int:
    """Accepts every scope item, as "Accept all" on Scope Review does; returns how many."""
    accepted = 0
    for item in scope_items(account, project_id):
        answer = account.client.call("PATCH", f"/scope-items/{item['id']}", {"status": "accepted"})
        accepted += answer.status == 200
    return accepted


def latest_ai_job(account: Account, project_id: str, kind: str) -> dict[str, Any] | None:
    """The newest AI job of a kind, or None when the project has none (404)."""
    answer = account.client.call(
        "GET", f"/projects/{project_id}/ai-jobs/latest", query={"kind": kind}
    )
    return answer.data if answer.status == 200 else None


def start_wbs_generation(account: Account, project_id: str) -> dict[str, Any]:
    """Starts WBS generation (a template without a key) and waits for the job to finish."""
    started = account.client.call("POST", f"/projects/{project_id}/wbs/generate")
    if started.status != 202:
        raise SuiteSetupError(f"Starting WBS generation failed: HTTP {started.status}")
    finished = wait_until(
        lambda: account.client.call(
            "GET", f"/projects/{project_id}/ai-jobs/latest", query={"kind": "wbs_generate"}
        ),
        lambda answer: (answer.data or {}).get("status") in JOB_DONE,
    )
    return finished.data or {}


def wbs_nodes(account: Account, project_id: str) -> list[dict[str, Any]]:
    """The project's WBS as a flat list of nodes."""
    return account.client.call("GET", f"/projects/{project_id}/wbs").data or []


def confirm_estimates(account: Account, project_id: str) -> None:
    """Confirms every AI estimate, as "Confirm all estimates" on step 4 does."""
    account.client.call("POST", f"/projects/{project_id}/wbs/confirm-estimates", {})


def add_required_skills(account: Account, project_id: str, names: list[str]) -> None:
    """Adds Required skills to the project's list (step 5)."""
    for name in names:
        answer = account.client.call(
            "POST", f"/projects/{project_id}/skills", {"name": name, "required": True}
        )
        if answer.status != 201:
            raise SuiteSetupError(f"Adding skill {name!r} failed: HTTP {answer.status}")


def answer_questions(account: Account, project_id: str) -> None:
    """Answers the step 5 questions (the deadline can move, no outside specialists)."""
    path = f"/projects/{project_id}/constraints"
    current = account.client.call("GET", path).data or {}
    body = constraint_body(
        current, deadlineAnswer="soft", deadlineMode="soft", specialistAnswer="no"
    )
    saved = account.client.call("PUT", path, body)
    if saved.status != 200:
        raise SuiteSetupError(f"Saving the constraint answers failed: HTTP {saved.status}")


# ───────────────────────────── The browser ─────────────────────────────


@contextmanager
def browser_page(settings: BrowserSettings) -> Iterator[Page]:
    """A Chromium page for the web app that can reach no API but the configured one.

    Web security is off so a dev server on any port may call the local API and Auth without
    adding its origin to their CORS lists; these tests never run against production.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not settings.headed, args=["--disable-web-security"]
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.route(re.compile(r".*/api/v1/.*"), lambda route: only_api(route, settings))
        page = context.new_page()
        try:
            yield page
        finally:
            # A test's slowed-down requests may still be waiting; let them go quietly.
            page.unroute_all(behavior="ignoreErrors")
            context.close()
            browser.close()


def only_api(route: Route, settings: BrowserSettings) -> None:
    """Lets API calls to the configured API through and aborts any other (e.g. a paid-key API)."""
    if route.request.url.startswith(f"{settings.api_url}/"):
        route.fallback()
        return
    print(f"  · refused a call to another API: {route.request.url}", flush=True)
    route.abort("blockedbyclient")


def sign_in_page(page: Page, settings: BrowserSettings, account: Account) -> None:
    """Signs in through the login screen and waits for the dashboard."""
    page.goto(f"{settings.web_url}/login", wait_until="domcontentloaded")
    page.fill("#li-email", account.email)
    page.fill("#li-pw", account.password)
    page.click("#btn-signin")
    page.wait_for_url("**/dashboard**", timeout=SCREEN_TIMEOUT_MS)


def open_step(page: Page, label: str) -> None:
    """Clicks a planning step in the sidebar, as a user moves between steps (no page reload)."""
    page.locator("button.nav", has_text=label).first.click()


def save_screenshot(page: Page, settings: BrowserSettings, name: str) -> Path:
    """Saves a full-page screenshot in the report folder; returns its path."""
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    path = settings.report_dir / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.png"
    page.screenshot(path=str(path), full_page=True)
    return path
