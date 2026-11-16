"""Browser regression test: step 6 never shows an out-of-date inputs sheet as current (C1).

Purpose: The product owner added the team on step 5, went straight to Optimization Results and
read "nobody has Maps, React Native, … yet" although the team covered everything but Maps; a
full page refresh fixed it. The cached sheet from before the team change was shown as current
while the hosted API was still rebuilding it. This test adds the team through the UI with the
sheet slowed down the way the hosted API is, then checks that step 6 says it is checking the
latest inputs (with Approve disabled) until the fresh sheet arrives, and never presents the old
"Required skills" row as current. Two paths: back to step 6 within 30 seconds after the saves,
and an add followed straight away by the step 6 click while the save is still on its way.
How it fits: Run by hand like the API suite (see README.md in this folder); builds its project
through the public API with `browser_support` (rules intake, template WBS: no Claude tokens);
layer: tests.
Created: 2026-11-15 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

import browser_support as support
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Route

TITLE = "Step 6 shows fresh inputs (C1)"
REQUIRED_SKILLS = [
    "Maps",
    "React Native",
    "Accessibility",
    "Mobile Development",
    "Business Analysis",
    "QA Testing",
]
# The hosted API (free tier, small database pool) can take seconds to build the sheet and to
# answer a save; these delays reproduce that locally.
SLOW_SHEET_MS = 2500
SLOW_SAVE_MS = 1500
SAMPLE_EVERY_MS = 100
SETTLE_TIMEOUT_S = 30.0
# One atomic read of what the confirmation sheet shows right now.
SAMPLE_SCRIPT = """() => {
  const row = [...document.querySelectorAll('#confirm-rows tr')]
    .find((tableRow) => tableRow.cells[0]?.textContent.trim() === 'Required skills')
  const approve = document.querySelector('#btn-approve-inputs')
  return {
    skillsRow: row ? row.cells[1].textContent.trim() : null,
    checking: document.querySelector('#confirm-checking') !== null,
    approveDisabled: approve ? approve.disabled : null,
  }
}"""


@dataclass(frozen=True)
class Member:
    """A person added through the step 5 member form."""

    name: str
    role: str
    skills: tuple[str, ...]


@dataclass(frozen=True)
class Sample:
    """What the sheet showed at one moment."""

    skills_row: str | None
    checking: bool
    approve_disabled: bool | None


FIRST_TEAM = (
    Member("Mia Chen", "Mobile Developer", ("React Native", "Mobile Development", "Accessibility")),
    Member("Omar Haddad", "Business Analyst", ("Business Analysis", "QA Testing")),
)
MAPS_MEMBER = Member("Lena Park", "GIS Developer", ("Maps",))


def build_project(account: support.Account) -> str:
    """A project ready for step 6 but for its team: WBS confirmed, six Required skills."""
    project_id = support.create_project(account, f"Browser C1 stale inputs {time.time():.0f}")
    support.run_rules_intake(account, project_id)
    support.accept_all_scope(account, project_id)
    job = support.start_wbs_generation(account, project_id)
    if job.get("status") != "succeeded":
        raise support.SuiteSetupError(f"Template WBS generation failed: {job}")
    support.confirm_estimates(account, project_id)
    support.add_required_skills(account, project_id, REQUIRED_SKILLS)
    support.answer_questions(account, project_id)
    return project_id


def server_skills_row(account: support.Account, project_id: str) -> str:
    """The "Required skills" value of the sheet as the API builds it right now."""
    sheet = account.client.call("GET", f"/projects/{project_id}/optimization/inputs").data or {}
    row = next((row for row in sheet.get("rows", []) if row.get("key") == "skills"), {})
    return str(row.get("value", ""))


def slow_down_api(page: Page, settings: support.BrowserSettings) -> None:
    """Delays the sheet and every team save the way the hosted API is slow."""
    project_path = f"{settings.api_url}/api/v1/projects/*"
    page.route(f"{project_path}/optimization/inputs", lambda route: slow_sheet(page, route))
    page.route(f"{project_path}/team", lambda route: slow_save(page, route))


def slow_sheet(page: Page, route: Route) -> None:
    """Holds the sheet request before the API sees it (a busy server builds it late)."""
    try:
        page.wait_for_timeout(SLOW_SHEET_MS)
        route.fallback()
    except PlaywrightError:
        pass  # The screen cancelled the request meanwhile, or the test is over.


def slow_save(page: Page, route: Route) -> None:
    """Lets the API save a new team member at once but delivers its answer late."""
    if route.request.method != "POST":
        route.fallback()
        return
    try:
        answer = route.fetch()
        page.wait_for_timeout(SLOW_SAVE_MS)
        route.fulfill(response=answer)
    except PlaywrightError:
        pass  # The page went away meanwhile.


def add_member(page: Page, member: Member, wait_for_save: bool) -> None:
    """Adds a person with the step 5 form, waiting for the save only when asked."""
    page.click("#btn-add-member")
    page.fill("#mf-name", member.name)
    page.fill("#mf-role", member.role)
    page.fill("#mf-hours", "30")
    for skill in member.skills:
        page.locator("#member-form label.pick", has_text=skill).locator("input").check()
    page.click("#mf-save")
    if wait_for_save:
        page.wait_for_selector("#member-form", state="detached", timeout=support.SCREEN_TIMEOUT_MS)


def read_sheet(page: Page) -> Sample:
    """What the confirmation sheet shows right now."""
    shown = page.evaluate(SAMPLE_SCRIPT)
    return Sample(shown["skillsRow"], shown["checking"], shown["approveDisabled"])


def watch_step6(page: Page, expected: str) -> tuple[list[Sample], Sample | None]:
    """Samples the sheet until it shows `expected` as current; returns all samples and the last."""
    samples: list[Sample] = []
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while time.monotonic() < deadline:
        sample = read_sheet(page)
        samples.append(sample)
        if sample.skills_row == expected and not sample.checking:
            return samples, sample
        page.wait_for_timeout(SAMPLE_EVERY_MS)
    return samples, samples[-1] if samples else None


def judge_step6(log: support.CheckLog, path: str, samples: list[Sample], expected: str) -> None:
    """Logs the verdicts of one path from its samples."""
    stale = [
        sample.skills_row
        for sample in samples
        if sample.skills_row is not None and not sample.checking and sample.skills_row != expected
    ]
    checking_count = sum(sample.checking for sample in samples)
    log.check(
        f"{path}: the old Required skills row is never presented as current",
        not stale,
        f"shown as current: {stale[0]!r} ({len(stale)} of {len(samples)} samples)"
        if stale
        else f"{len(samples)} samples, 'Checking the latest inputs' in {checking_count}",
    )
    unverified = [
        sample
        for sample in samples
        if sample.approve_disabled is False and (sample.checking or sample.skills_row != expected)
    ]
    log.check(
        f"{path}: Approve inputs & run is disabled until the fresh sheet is shown",
        not unverified,
        f"enabled with {unverified[0]}" if unverified else "",
    )
    last = samples[-1] if samples else None
    settled = last is not None and last.skills_row == expected and not last.checking
    log.check(
        f"{path}: the sheet settles on the server's row, ready to approve",
        settled and last is not None and last.approve_disabled is False,
        f"expected {expected!r}, last shown {last}",
    )


def run(settings: support.BrowserSettings, log: support.CheckLog) -> None:
    """Builds the project, then drives both paths in one browser session."""
    account = support.sign_in_account(settings)
    project_id = build_project(account)
    log.note(f"Account {account.email}, project {project_id}, web app {settings.web_url}")
    with support.browser_page(settings) as page:
        support.sign_in_page(page, settings, account)
        page.goto(f"{settings.web_url}/projects/{project_id}/optimization")
        page.wait_for_selector("#confirm-rows", timeout=support.SCREEN_TIMEOUT_MS)
        before = read_sheet(page).skills_row or ""
        log.check(
            "Setup: step 6 first lists every Required skill as missing",
            all(skill in before for skill in REQUIRED_SKILLS),
            before,
        )
        slow_down_api(page, settings)

        # Path 1: add the team on step 5 (each save confirmed), then straight back to step 6.
        support.open_step(page, "Constraints")
        page.wait_for_selector("#btn-add-member", timeout=support.SCREEN_TIMEOUT_MS)
        for member in FIRST_TEAM:
            add_member(page, member, wait_for_save=True)
        support.open_step(page, "Optimization Results")
        expected = server_skills_row(account, project_id)
        samples, _ = watch_step6(page, expected)
        judge_step6(log, "Back within 30 s", samples, expected)
        if not log.check("Path 1 ends on 'nobody has Maps'", "nobody has Maps yet" in expected):
            support.save_screenshot(page, settings, "c1-path1")

        # Path 2: add the Maps person and click step 6 before the save has answered.
        support.open_step(page, "Constraints")
        page.wait_for_selector("#btn-add-member", timeout=support.SCREEN_TIMEOUT_MS)
        add_member(page, MAPS_MEMBER, wait_for_save=False)
        support.open_step(page, "Optimization Results")
        page.wait_for_timeout(SLOW_SAVE_MS // 3)
        expected = server_skills_row(account, project_id)
        samples, last = watch_step6(page, expected)
        judge_step6(log, "Add, then step 6 at once", samples, expected)
        if last is None or last.skills_row != expected:
            support.save_screenshot(page, settings, "c1-path2")
        log.check("Path 2 ends with every skill covered", "all covered" in expected, expected)


def main() -> int:
    """Runs the test; exit code 0 = passed, 1 = a check failed, 2 = setup failed."""
    log = support.CheckLog(TITLE)
    try:
        settings = support.load_settings()
        run(settings, log)
    except (support.SuiteSetupError, OSError) as error:
        print(f"SETUP FAILED: {error}", file=sys.stderr)
        return 2
    return log.exit_code()


if __name__ == "__main__":
    sys.exit(main())
