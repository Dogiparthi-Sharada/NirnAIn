"""Entry point of the NirnAIn API end-to-end suite: signs in, runs every area, writes the report.

Purpose: One command that signs in as the QA account (or a brand-new one), creates fake
projects in its workspace and walks every planning step, every mid-project change and the
round-2 decisions against a running API, logging each scenario even when an area crashes,
then counts the Claude calls the API made (must be 0) and exits non-zero on any FAIL.
How it fits: run it from anywhere with `python3.12 tests/e2e/api/run_api_suite.py`; it wires
`suite_settings`, `suite_client` and `suite_report` to the `scenarios_*` modules; see
README.md in this folder; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import secrets
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scenarios_corner_cases import (
    access_scenarios,
    ai_fallback_scenarios,
    change_corner_cases,
    impossible_project_scenarios,
)
from scenarios_create_intake_scope import (
    create_project_scenarios,
    intake_scenarios,
    scope_scenarios,
)
from scenarios_dependencies_constraints import constraint_scenarios, dependency_scenarios
from scenarios_midproject import change_scenarios
from scenarios_optimize_apply import optimize_and_apply
from scenarios_round2_main_project import direct_edit_scenarios, worker_type_scenarios
from scenarios_round2_small_projects import required_skill_scenarios, today_and_done_scenarios
from scenarios_team_skills import skills_scenarios, team_scenarios
from scenarios_wbs import wbs_scenarios
from suite_client import ApiClient, AuthClient, SuiteSetupError, throwaway_password
from suite_fixtures import QaProject
from suite_report import ScenarioLog
from suite_settings import SuiteSettings, parse_settings

TITLE = "NirnAIn — API end-to-end scenarios (fake data, no Claude tokens)"
CLAUDE_HOST = "api.anthropic.com"
ACCESS_AREA = "9. Access control and AI fallbacks"
# Steps 2-5 on the main project, in the order a project manager works through them.
PROJECT_AREAS: tuple[tuple[str, Callable[[ApiClient, ScenarioLog, QaProject], None]], ...] = (
    ("2. AI intake and uploads (no Claude: rules)", intake_scenarios),
    ("3. Scope review (edit requirements)", scope_scenarios),
    ("4. WBS builder", wbs_scenarios),
    ("5a. Team & resources", team_scenarios),
    ("5e. Worker type: employee or contractor (D1)", worker_type_scenarios),
    ("5b. Required skills", skills_scenarios),
    ("5c. Dependencies", dependency_scenarios),
    ("5d. Constraints (Must hold / Can flex) and questions", constraint_scenarios),
)


@dataclass(frozen=True)
class SignedIn:
    """The clients the scenarios use and the accounts behind them."""

    client: ApiClient
    outsider: ApiClient
    anonymous: ApiClient
    qa_email: str
    outsider_email: str


def claude_calls(api_log: Path | None) -> int | None:
    """Requests to Claude recorded in the API log so far; None when no log was given."""
    if api_log is None:
        return None
    return api_log.read_text(encoding="utf-8", errors="replace").count(CLAUDE_HOST)


def isolated[ResultT](log: ScenarioLog, area: str, step: Callable[[], ResultT]) -> ResultT | None:
    """Runs one area; an unexpected exception becomes a FAIL row instead of stopping the suite."""
    try:
        return step()
    # Any crash (in the test or the API) is recorded as a FAIL row so the other areas still run.
    except Exception as error:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        where = f"{Path(frame.filename).name}:{frame.lineno}"
        actual = f"{type(error).__name__}: {error} @ {where}"
        log.check(area, "Unexpected error in the test itself or the API", "no crash", actual, False)
        return None


def throwaway_email(settings: SuiteSettings, role: str) -> str:
    """A unique address for an account created just for this run."""
    stamp = f"{datetime.now():%Y%m%d%H%M%S}.{secrets.token_hex(2)}"
    return f"{role}.{stamp}@{settings.fresh_email_domain}"


def sign_in_accounts(settings: SuiteSettings) -> SignedIn:
    """Signs in the QA account and an outsider, signing up throwaway accounts where asked."""
    auth = AuthClient(settings.auth_url, settings.anon_key)
    qa_email, qa_password = settings.qa_email, settings.qa_password
    if qa_password is None:
        qa_email, qa_password = throwaway_email(settings, "qa.run"), throwaway_password()
        auth.sign_up(qa_email, qa_password, "QA Run")
    outsider_email, outsider_password = settings.outsider_email, settings.outsider_password
    if outsider_password is None:
        outsider_email, outsider_password = (
            throwaway_email(settings, "qa.outsider"),
            throwaway_password(),
        )
        auth.sign_up(outsider_email, outsider_password, "QA Outsider")
    return SignedIn(
        client=ApiClient(settings.api_url, auth.sign_in(qa_email, qa_password)),
        outsider=ApiClient(settings.api_url, auth.sign_in(outsider_email, outsider_password)),
        anonymous=ApiClient(settings.api_url, None),
        qa_email=qa_email,
        outsider_email=outsider_email,
    )


def workspace_id(client: ApiClient) -> str:
    """The QA account's first workspace (its own after sign-up)."""
    me = client.call("GET", "/me")
    workspaces = []
    if me.status == 200 and isinstance(me.data, dict):
        workspaces = me.data.get("organizations") or []
    if not workspaces:
        raise SuiteSetupError(f"GET /me answered HTTP {me.status} without a workspace")
    return str(workspaces[0]["id"])


def run_areas(signed_in: SignedIn, log: ScenarioLog, org_id: str, project_name: str) -> None:
    """Runs every area in order; areas that need Plan v1 run only when it was applied."""
    client = signed_in.client
    project = isolated(
        log,
        "1. Create project",
        lambda: create_project_scenarios(client, log, org_id, project_name),
    )
    if project is None:
        return
    for area, step in PROJECT_AREAS:
        isolated(log, area, lambda step=step: step(client, log, project))
    applied_run = isolated(
        log, "6. Optimization results and Plan v1", lambda: optimize_and_apply(client, log, project)
    )
    if applied_run:
        isolated(
            log,
            "7a. After Plan v1: inputs stay editable (D3) and slips are explained (D2)",
            lambda: direct_edit_scenarios(client, log, project, applied_run),
        )
        isolated(
            log,
            "7b. Mid-project changes (step 7)",
            lambda: change_scenarios(client, log, project, applied_run),
        )
        isolated(log, "7e. Change corner cases", lambda: change_corner_cases(client, log, project))
    isolated(
        log,
        "8. Over-constrained project (corner case)",
        lambda: impossible_project_scenarios(client, log, org_id),
    )
    isolated(
        log,
        "8b. Re-plan from today; finished work stays (D4)",
        lambda: today_and_done_scenarios(client, log, org_id),
    )
    isolated(
        log,
        "8c. Required skills reach the optimizer (D6)",
        lambda: required_skill_scenarios(client, log, org_id),
    )
    isolated(
        log,
        ACCESS_AREA,
        lambda: access_scenarios(
            signed_in.anonymous, signed_in.outsider, log, org_id, project.project_id
        ),
    )
    isolated(
        log, ACCESS_AREA, lambda: ai_fallback_scenarios(client, log, org_id, project.project_id)
    )


def token_guard(log: ScenarioLog, api_log: Path | None, calls_before: int | None) -> None:
    """Logs how many Claude calls the API made during the run (INFO when there is no log)."""
    area, scenario = "10. Token guard", "Claude calls made by the whole suite"
    calls_after = claude_calls(api_log)
    if calls_before is None or calls_after is None:
        log.check(area, scenario, "0", "not checked: pass --qa-api-log to count them", None)
        return
    made = calls_after - calls_before
    log.check(area, scenario, "0", str(made), made == 0)


def main(argv: list[str]) -> int:
    """Runs the suite; exit code 0 = all passed, 1 = a FAIL or BLOCKED row, 2 = setup failed."""
    settings = parse_settings(argv)
    try:
        if settings.qa_api_log is not None and not settings.qa_api_log.is_file():
            raise SuiteSetupError(f"--qa-api-log {settings.qa_api_log} is not a file")
        calls_before = claude_calls(settings.qa_api_log)
        signed_in = sign_in_accounts(settings)
        org_id = workspace_id(signed_in.client)
    except (SuiteSetupError, OSError) as error:
        print(f"SETUP FAILED: {error}", file=sys.stderr)
        return 2
    settings.report_path.parent.mkdir(parents=True, exist_ok=True)
    log = ScenarioLog(settings.report_path, TITLE)
    log.notes += [
        f"Account {signed_in.qa_email}, workspace {org_id}. API under test: {settings.api_url} "
        "(expected without a Claude key: rules and templates).",
        f"Outsider {signed_in.outsider_email} is used only for read-only cross-workspace checks.",
    ]
    log.write()
    run_areas(signed_in, log, org_id, settings.project_name)
    token_guard(log, settings.qa_api_log, calls_before)
    counts = log.counts()
    print(" · ".join(f"{verdict} {count}" for verdict, count in counts.items()))
    print(f"Report: {settings.report_path}")
    return 1 if counts["FAIL"] or counts["BLOCKED"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
