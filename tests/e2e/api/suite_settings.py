"""Command line and environment settings of the API end-to-end suite.

Purpose: Collect every setting (URLs, the anon key, accounts, report folder, Claude-call
guard) from CLI flags or NIRNAIN_* environment variables, with safe defaults for the local
stack, so nothing machine-specific or secret lives in the code.
How it fits: `run_api_suite.py` calls `parse_settings` first and passes the frozen
`SuiteSettings` to the sign-in and reporting code; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_API_URL = "http://127.0.0.1:58001"
DEFAULT_AUTH_URL = "http://localhost:54321"
DEFAULT_QA_EMAIL = "qa@nirnain.local"
DEFAULT_OUTSIDER_EMAIL = "demo@nirnain.local"
DEFAULT_EMAIL_DOMAIN = "nirnain.local"
DEFAULT_PROJECT_NAME = "QA API - Data Platform Revamp"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parent / "reports"


@dataclass(frozen=True)
class SuiteSettings:
    """Everything one suite run needs; passwords are None when a throwaway account is used."""

    api_url: str
    auth_url: str
    anon_key: str
    qa_email: str
    qa_password: str | None
    outsider_email: str
    outsider_password: str | None
    fresh_workspace: bool
    fresh_email_domain: str
    project_name: str
    report_path: Path
    qa_api_log: Path | None


def build_parser() -> argparse.ArgumentParser:
    """The suite's command line; every flag falls back to a NIRNAIN_* environment variable."""
    env = os.environ.get
    parser = argparse.ArgumentParser(
        description="Run the NirnAIn API end-to-end scenarios against a running API.",
        epilog="Passwords are read only from the environment: NIRNAIN_QA_PASSWORD and "
        "NIRNAIN_OUTSIDER_PASSWORD. See tests/e2e/api/README.md.",
    )
    parser.add_argument(
        "--api-url",
        default=env("NIRNAIN_API_URL", DEFAULT_API_URL),
        help="API base URL without /api/v1 (env NIRNAIN_API_URL)",
    )
    parser.add_argument(
        "--auth-url",
        default=env("NIRNAIN_AUTH_URL", DEFAULT_AUTH_URL),
        help="Supabase URL that serves /auth/v1 (env NIRNAIN_AUTH_URL)",
    )
    parser.add_argument(
        "--anon-key",
        default=env("NIRNAIN_ANON_KEY"),
        help="Supabase anon / publishable key, never the service-role key (env NIRNAIN_ANON_KEY)",
    )
    parser.add_argument(
        "--qa-email",
        default=env("NIRNAIN_QA_EMAIL", DEFAULT_QA_EMAIL),
        help="QA account that owns the test projects (env NIRNAIN_QA_EMAIL)",
    )
    parser.add_argument(
        "--fresh-workspace",
        action="store_true",
        help="Sign up a brand-new QA account (and workspace) for this run",
    )
    parser.add_argument(
        "--fresh-email-domain",
        default=env("NIRNAIN_FRESH_EMAIL_DOMAIN", DEFAULT_EMAIL_DOMAIN),
        help="Domain of the throwaway accounts (env NIRNAIN_FRESH_EMAIL_DOMAIN)",
    )
    parser.add_argument(
        "--project-name",
        default=env("NIRNAIN_QA_PROJECT_NAME", DEFAULT_PROJECT_NAME),
        help="Name of the main test project (env NIRNAIN_QA_PROJECT_NAME)",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path(env("NIRNAIN_E2E_REPORT_DIR", str(DEFAULT_REPORT_DIR))),
        help="Folder for the markdown report (env NIRNAIN_E2E_REPORT_DIR)",
    )
    parser.add_argument(
        "--qa-api-log",
        type=Path,
        default=env("NIRNAIN_QA_API_LOG") or None,
        help="Log file of the API under test; Claude calls in it are counted (must stay 0). "
        "Skipped when not given (env NIRNAIN_QA_API_LOG)",
    )
    return parser


def parse_settings(argv: list[str]) -> SuiteSettings:
    """Parses flags and environment; exits with a usage error when a required value is missing."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if not arguments.anon_key:
        parser.error("missing --anon-key (or NIRNAIN_ANON_KEY)")
    qa_password = os.environ.get("NIRNAIN_QA_PASSWORD")
    if not arguments.fresh_workspace and not qa_password:
        parser.error("set NIRNAIN_QA_PASSWORD for the QA account, or pass --fresh-workspace")
    report_name = f"api-suite-{datetime.now():%Y%m%d-%H%M%S}.md"
    return SuiteSettings(
        api_url=arguments.api_url,
        auth_url=arguments.auth_url,
        anon_key=arguments.anon_key,
        qa_email=arguments.qa_email,
        qa_password=None if arguments.fresh_workspace else qa_password,
        outsider_email=os.environ.get("NIRNAIN_OUTSIDER_EMAIL", DEFAULT_OUTSIDER_EMAIL),
        outsider_password=os.environ.get("NIRNAIN_OUTSIDER_PASSWORD") or None,
        fresh_workspace=arguments.fresh_workspace,
        fresh_email_domain=arguments.fresh_email_domain,
        project_name=arguments.project_name,
        report_path=arguments.report_dir.resolve() / report_name,
        qa_api_log=arguments.qa_api_log,
    )
