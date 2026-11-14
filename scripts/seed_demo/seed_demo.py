"""Entry point of the NirnAIn demo seed: fills one demo account so the app looks like the mockup.

Purpose: Sign up (or sign in) the demo user through Supabase Auth and seed her workspace
through the public NirnAIn API: four portfolio projects at different stages and the
Enterprise Data Platform walkthrough through all 7 steps (Plan v1, then a Steering Committee
change approved as Plan v2). Idempotent: projects that already exist are kept unless
`--reset` is given. Standard library only; never writes to the database.
How it fits: run `python3.12 scripts/seed_demo/seed_demo.py --help`; settings come from flags
or NIRNAIN_* / DEMO_* environment variables (see README.md); it drives `seed_flows.Seeder`
with the specs from `seed_portfolio_data` and `seed_walkthrough_data`; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from datetime import date
from typing import Any

from seed_client import ApiClient, AuthSession, SeedError, wake_api
from seed_flows import AI_RULES, AI_SKIP, SeededProject, Seeder
from seed_model import DemoCalendar, ProjectSpec
from seed_portfolio_data import portfolio_specs
from seed_walkthrough_data import WALKTHROUGH_NAME, walkthrough_spec

DEFAULT_AUTH_URL = "http://localhost:54321"
DEFAULT_API_URL = "http://127.0.0.1:58001"
DEFAULT_EMAIL = "jane.doe@nirnain.demo"
DEMO_WORKSPACE = "Demo Workspace"
# Accounts other people test with; the seed must never sign in as them.
PROTECTED_ACCOUNTS = {"demo@nirnain.local", "qa@nirnain.local"}


def build_parser() -> argparse.ArgumentParser:
    """The seed's command line; flags fall back to NIRNAIN_* and DEMO_* environment variables."""
    env = os.environ.get
    parser = argparse.ArgumentParser(
        description="Seed the NirnAIn demo account through the public API.",
        epilog="The password is read only from DEMO_PASSWORD. See scripts/seed_demo/README.md.",
    )
    parser.add_argument(
        "--auth-url",
        "--supabase-url",
        dest="auth_url",
        default=env("NIRNAIN_AUTH_URL", DEFAULT_AUTH_URL),
        help="Supabase URL that serves /auth/v1 (env NIRNAIN_AUTH_URL)",
    )
    parser.add_argument(
        "--api-url",
        default=env("NIRNAIN_API_URL", DEFAULT_API_URL),
        help="NirnAIn API base URL, without /api/v1 (env NIRNAIN_API_URL)",
    )
    parser.add_argument(
        "--anon-key",
        "--publishable-key",
        dest="anon_key",
        default=env("NIRNAIN_ANON_KEY"),
        help="Supabase anon or publishable key, never the service-role key (env NIRNAIN_ANON_KEY)",
    )
    parser.add_argument("--email", default=env("DEMO_EMAIL", DEFAULT_EMAIL), help="env DEMO_EMAIL")
    parser.add_argument(
        "--full-name", default=env("DEMO_FULL_NAME", "Jane Doe"), help="env DEMO_FULL_NAME"
    )
    parser.add_argument(
        "--ai-calls",
        choices=(AI_RULES, AI_SKIP),
        default=env("DEMO_AI_CALLS"),
        help="'rules': the API has NO ANTHROPIC_API_KEY, so intake, WBS validation and notes "
        "use its built-in rules (checked after each call). 'skip': never call those "
        "endpoints (works with any API). Required (env DEMO_AI_CALLS).",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=int(env("DEMO_YEAR", "0")) or None,
        help="Year of the walkthrough (the mockup's 2025). "
        "Default: the first year whose Jan 15 is ahead (env DEMO_YEAR).",
    )
    parser.add_argument(
        "--reset", action="store_true", help="Delete and rebuild demo projects that already exist"
    )
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parses flags and environment; exits with a usage error when a required value is missing."""
    parser = build_parser()
    args = parser.parse_args(argv)
    args.password = os.environ.get("DEMO_PASSWORD")
    required = (
        ("--anon-key (NIRNAIN_ANON_KEY)", args.anon_key),
        ("DEMO_PASSWORD", args.password),
        ("--ai-calls (DEMO_AI_CALLS)", args.ai_calls),
    )
    missing = [name for name, value in required if not value]
    if missing:
        parser.error(f"missing {', '.join(missing)}")
    if args.ai_calls not in (AI_RULES, AI_SKIP):
        parser.error(f"--ai-calls must be '{AI_RULES}' or '{AI_SKIP}', not '{args.ai_calls}'")
    if args.email.lower() in PROTECTED_ACCOUNTS:
        parser.error(
            f"{args.email} is a shared test account; seed a dedicated demo account instead"
        )
    return args


def sign_in(
    args: argparse.Namespace, say: Callable[[str], None]
) -> tuple[ApiClient, dict[str, Any]]:
    """Signs the demo user in (signing up first if needed); returns the client and /me."""
    session = AuthSession(args.auth_url, args.anon_key)
    how = session.sign_up_or_in(args.email, args.password, args.full_name)
    wake_api(args.api_url)
    api = ApiClient(args.api_url, session)
    me = api.expect("GET", "/me", step="Read /me")
    if me.get("fullName") != args.full_name:
        me = api.expect("PATCH", "/me", {"fullName": args.full_name}, step="Set the full name")
    say(f"{args.email}: {how} as {me.get('fullName')}")
    return api, me


def workspace_of(me: dict[str, Any], say: Callable[[str], None]) -> dict[str, Any]:
    """The workspace the user owns (their personal one after sign-up)."""
    workspaces = me.get("organizations") or []
    owned = [
        workspace for workspace in workspaces if workspace.get("role") == "owner"
    ] or workspaces
    if not owned:
        raise SeedError("The demo user has no workspace")
    workspace: dict[str, Any] = owned[0]
    if workspace["name"] != DEMO_WORKSPACE:
        say(
            f"Workspace stays '{workspace['name']}': the API cannot rename a workspace "
            "(no organization update endpoint; PATCH /me changes only the profile)."
        )
    return workspace


def seed_all(
    seeder: Seeder, specs: list[ProjectSpec], reset: bool, say: Callable[[str], None]
) -> list[SeededProject]:
    """Seeds each spec, keeping (or with --reset, rebuilding) projects that already exist."""
    seeded: list[SeededProject] = []
    for spec in specs:
        existing = seeder.find(spec.name)
        if existing and not reset:
            project = existing[0]
            say(f"  {spec.name} ({project['code']}): already there, kept (use --reset to rebuild)")
            versions = seeder.versions(project["id"])
            seeded.append(
                SeededProject(
                    spec.name, project["id"], project["code"], spec.stage, versions, kept=True
                )
            )
            continue
        for project in existing:
            seeder.delete(project)
            say(f"  {spec.name} ({project['code']}): deleted (--reset)")
        seeded.append(seeder.seed(spec))
    return seeded


def print_summary(api: ApiClient, seeded: list[SeededProject], workspace: dict[str, Any]) -> None:
    """One line per seeded project: name, code, status, plan versions and id."""
    print(f"\nSeeded workspace '{workspace['name']}' ({workspace['id']}):")
    print(f"  {'Project':<26} {'Code':<6} {'Status':<10} {'Plans':<7} Id")
    for project in seeded:
        current = api.expect("GET", f"/projects/{project.project_id}", step="Summary")
        plans = ",".join(f"v{version}" for version in project.versions) or "—"
        kept = "  (kept)" if project.kept else ""
        print(
            f"  {project.name:<26} {project.code:<6} {current['status']:<10} {plans:<7} "
            f"{project.project_id}{kept}"
        )
        for note in project.notes:
            print(f"      {note}")


def say(line: str) -> None:
    """Prints a progress line, flushed so a long run shows where it is."""
    print(line, flush=True)


def main(argv: list[str]) -> int:
    """Runs the seed; exit code 0 on success, 1 with a `FAILED: <step>` line otherwise."""
    args = parse_args(argv)
    try:
        api, me = sign_in(args, say)
        workspace = workspace_of(me, say)
        calendar = DemoCalendar.starting(date.today(), args.year)
        say(
            f"Walkthrough year {calendar.year}; plans start from today, {calendar.today_label}. "
            f"AI calls: {args.ai_calls}."
        )
        seeder = Seeder(api, workspace["id"], me["id"], args.ai_calls, say)
        specs = [*portfolio_specs(calendar), walkthrough_spec(calendar)]
        seeded = seed_all(seeder, specs, args.reset, say)
        walkthrough = next(project for project in seeded if project.name == WALKTHROUGH_NAME)
        # The dashboard opens on the most recently updated project: make it the walkthrough.
        path = f"/projects/{walkthrough.project_id}"
        current = api.expect("GET", path, step="Touch walkthrough")
        api.expect("PATCH", path, {"progressPct": current["progressPct"]}, step="Touch walkthrough")
        print_summary(api, seeded, workspace)
    except SeedError as error:
        print(f"\nFAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
