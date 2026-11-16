"""Browser regression test: the WBS Builder never generates without accepted scope (C2).

Purpose: The product owner opened step 4 before the intake had run; the page started WBS
generation by itself, which failed with "Accept at least one scope item in Scope Review first".
After she accepted all 27 items, step 4 still showed that old failure until she clicked Try
again. Three scenarios: (a) without accepted scope, step 4 says to accept the scope first, links
to Scope Review and creates no generation job; (b) a generation that failed before the scope was
accepted is retried once by itself and the WBS appears without Try again; (c) a generation that
failed for another reason waits for Try again (no automatic retry, which could cost tokens).
How it fits: Run by hand like the API suite (see README.md in this folder); builds its projects
through the public API with `browser_support` (rules intake, template WBS: no Claude tokens);
layer: tests.
Created: 2026-11-15 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import sys
import time

import browser_support as support
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

TITLE = "WBS Builder empty state (C2)"
GENERATE = "wbs_generate"
# Long enough for an automatic start to show up in the API (the old page started at once).
QUIET_PERIOD_MS = 6000
WBS_TIMEOUT_MS = 60_000


def open_wbs(page: Page, settings: support.BrowserSettings, project_id: str) -> None:
    """Opens step 4 of a project and waits for the screen."""
    page.goto(f"{settings.web_url}/projects/{project_id}/wbs")
    page.wait_for_selector("#v-wbs", timeout=support.SCREEN_TIMEOUT_MS)


def try_again_button(page: Page) -> Locator:
    """The empty table's Try again button (shown after a failed generation)."""
    return page.locator("#v-wbs button.linkbtn", has_text="Try again")


def no_accepted_scope(
    settings: support.BrowserSettings, account: support.Account, page: Page, log: support.CheckLog
) -> None:
    """(a) Intake done, nothing accepted: an empty state that sends the PM to Scope Review."""
    project_id = support.create_project(account, f"Browser C2a no accepted scope {time.time():.0f}")
    items = support.run_rules_intake(account, project_id)
    log.note(f"(a) project {project_id}: {items} scope items, none accepted")
    open_wbs(page, settings, project_id)
    page.wait_for_timeout(QUIET_PERIOD_MS)
    notice = page.locator("#wbs-needs-scope")
    shown = notice.count() > 0 and "Accept your scope items first" in notice.inner_text()
    if not log.check("(a) Step 4 says to accept the scope items first", shown):
        support.save_screenshot(page, settings, "c2a-empty-state")
    job = support.latest_ai_job(account, project_id, GENERATE)
    log.check(
        "(a) No WBS generation job was created",
        job is None,
        f"latest job: {job.get('status')} {job.get('error')!r}" if job else "",
    )
    nodes = support.wbs_nodes(account, project_id)
    log.check("(a) The WBS is still empty", not nodes, f"{len(nodes)} nodes")
    if shown:
        page.click("#btn-open-scope")
        page.wait_for_url("**/scope", timeout=support.SCREEN_TIMEOUT_MS)
    log.check("(a) The button opens Scope Review", page.url.endswith("/scope"), page.url)


def failed_before_scope(
    settings: support.BrowserSettings, account: support.Account, page: Page, log: support.CheckLog
) -> None:
    """(b) Generation failed before the intake; scope accepted since: the WBS appears by itself."""
    project_id = support.create_project(
        account, f"Browser C2b failed then accepted {time.time():.0f}"
    )
    failed = support.start_wbs_generation(account, project_id)
    log.check(
        "(b) Setup: generating before the intake fails",
        failed.get("status") == "failed",
        f"{failed.get('status')}: {failed.get('error')!r}",
    )
    support.run_rules_intake(account, project_id)
    accepted = support.accept_all_scope(account, project_id)
    log.note(f"(b) project {project_id}: {accepted} scope items accepted after the failure")
    open_wbs(page, settings, project_id)
    try:
        page.wait_for_selector("#wbs-src", timeout=WBS_TIMEOUT_MS)
        appeared = True
    except PlaywrightTimeoutError:
        appeared = False
    retry_shown = try_again_button(page).count() > 0
    if not log.check("(b) The WBS appears without clicking Try again", appeared, page.url):
        support.save_screenshot(page, settings, "c2b-stale-failure")
    log.check("(b) The old failure is not shown", not retry_shown)
    job = support.latest_ai_job(account, project_id, GENERATE) or {}
    log.check(
        "(b) One new generation succeeded",
        job.get("id") != failed.get("id") and job.get("status") == "succeeded",
        f"latest job {job.get('id')} {job.get('status')}",
    )
    packages = [node for node in support.wbs_nodes(account, project_id) if node["level"] == 3]
    log.check("(b) The WBS has work packages", bool(packages), f"{len(packages)} packages")


def other_failure(
    settings: support.BrowserSettings, account: support.Account, page: Page, log: support.CheckLog
) -> None:
    """(c) Generation failed with the scope already accepted: Try again, no automatic retry."""
    project_id = support.create_project(account, f"Browser C2c other failure {time.time():.0f}")
    # An accepted requirement but no deliverable: the template has nothing to build from.
    added = account.client.call(
        "POST",
        f"/projects/{project_id}/scope-items",
        {"category": "requirement", "text": "Support single sign-on for every dashboard."},
    )
    item_id = (added.data or {}).get("id")
    account.client.call("PATCH", f"/scope-items/{item_id}", {"status": "accepted"})
    failed = support.start_wbs_generation(account, project_id)
    log.check(
        "(c) Setup: generating from a requirement only fails",
        failed.get("status") == "failed",
        f"{failed.get('status')}: {failed.get('error')!r}",
    )
    open_wbs(page, settings, project_id)
    page.wait_for_timeout(QUIET_PERIOD_MS)
    if not log.check("(c) The failure is shown with Try again", try_again_button(page).count() > 0):
        support.save_screenshot(page, settings, "c2c-other-failure")
    job = support.latest_ai_job(account, project_id, GENERATE) or {}
    log.check(
        "(c) Nothing was retried automatically",
        job.get("id") == failed.get("id"),
        f"latest job {job.get('id')} {job.get('status')}",
    )


def main() -> int:
    """Runs the three scenarios; exit code 0 = passed, 1 = a check failed, 2 = setup failed."""
    log = support.CheckLog(TITLE)
    try:
        settings = support.load_settings()
        account = support.sign_in_account(settings)
        log.note(f"Account {account.email}, web app {settings.web_url}")
        with support.browser_page(settings) as page:
            support.sign_in_page(page, settings, account)
            no_accepted_scope(settings, account, page, log)
            failed_before_scope(settings, account, page, log)
            other_failure(settings, account, page, log)
    except (support.SuiteSetupError, OSError) as error:
        print(f"SETUP FAILED: {error}", file=sys.stderr)
        return 2
    return log.exit_code()


if __name__ == "__main__":
    sys.exit(main())
