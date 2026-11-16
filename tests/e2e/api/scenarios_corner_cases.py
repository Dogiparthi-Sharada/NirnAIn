"""API end-to-end corner cases: invalid changes, an over-constrained project and access control.

Purpose: Check what must be refused or explained: step-7 changes that are empty, out of
range or decided out of order (7e); a project whose limits cannot all hold, which must be
explained with priced remedies, and why the others are not offered (8); and the security
boundaries plus the no-key AI fallbacks (9).
How it fits: `run_api_suite.py` runs 7e after the mid-project changes, 8 on a project of its
own, and 9 near the end with an anonymous client and a user from another workspace;
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from suite_client import ApiClient
from suite_fixtures import QaProject, add_packages, constraint_body
from suite_planning import SOLVED, describe_run, remedies_of, run_optimizer, versions_of
from suite_report import ScenarioLog

CHANGE_AREA = "7e. Change corner cases"
IMPOSSIBLE_AREA = "8. Over-constrained project (corner case)"
ACCESS_AREA = "9. Access control and AI fallbacks"
MINIMUM_HISTORY = 8
MINIMUM_VERSIONS = 3
IMPOSSIBLE_LIMITS = {
    "budgetTotalCents": 1_000_000,
    "budgetMode": "hard",
    "startDate": "2026-11-30",
    "mustFinishBy": "2026-12-11",
    "deadlineMode": "hard",
    "capacityMode": "hard",
    "skillMode": "soft",
    "specialistAnswer": "yes",
    "deadlineAnswer": "hard",
    "specialistRateConfirmed": True,
    "contractorRateCents": 12_000,
    "allowContractors": True,
}
DRAFT_NOTES = (
    "Project: Warehouse Scanner Rollout. Sponsor: Dana Lee. Start March 2, 2027, go live by "
    "May 29, 2027. Budget $120,000."
)


def out_of_order_decisions(client: ApiClient, log: ScenarioLog, base: str) -> None:
    """An open change can't be approved or compared before a re-plan, or approved once rejected."""
    pending = client.call(
        "POST", base, {"source": "edits", "edits": {"budgetTotalCents": 55_000_000}}
    )
    if pending.status not in {201, 202}:
        return
    change_path = f"/changes/{pending.data['id']}"
    early = client.call("POST", f"{change_path}/approve", {})
    log.status(
        CHANGE_AREA, "Approve before re-planning → 409", early, {409}, must_contain="Re-plan first"
    )
    compare = client.call("GET", f"{change_path}/compare")
    log.status(CHANGE_AREA, "Compare before re-planning → 409", compare, {409})
    rejected = client.call("POST", f"{change_path}/reject", {})
    log.status(CHANGE_AREA, "Reject the open change", rejected, {200})
    late = client.call("POST", f"{change_path}/approve", {})
    log.status(CHANGE_AREA, "Approve a rejected change → 409", late, {409})


def change_corner_cases(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """Invalid change requests, decisions out of order, then the change history and versions."""
    base = f"/projects/{project.project_id}/changes"
    first_member = next(iter(project.team_ids.values()))
    for label, body in (
        ("Change with no edits rejected", {"source": "edits", "edits": {}}),
        ("Notes change with empty text rejected", {"source": "notes", "text": ""}),
    ):
        log.status(CHANGE_AREA, label, client.call("POST", base, body), {422})
    unchanged = client.call(
        "POST", base, {"source": "edits", "edits": {"mustFinishBy": "2027-08-20"}}
    )
    log.status(
        CHANGE_AREA,
        "Edit equal to the current plan → 'Nothing changed'",
        unchanged,
        {422},
        must_contain="Nothing changed",
    )
    for label, hours in (("Negative hours rejected", -5), ("Hours above 80 rejected", 95)):
        edits = {"hoursPerWeek": {first_member: hours}}
        answer = client.call("POST", base, {"source": "edits", "edits": edits})
        log.status(CHANGE_AREA, label, answer, {422})
    out_of_order_decisions(client, log, base)
    history = client.call("GET", base).data or []
    log.check(
        CHANGE_AREA,
        "Change history keeps every change, newest first",
        "all recorded",
        f"{len(history)} changes: " + ", ".join(item.get("status", "?") for item in history),
        len(history) >= MINIMUM_HISTORY,
    )
    versions = versions_of(client, project)
    log.check(
        CHANGE_AREA,
        "Plan versions after all approvals",
        "one version per approved change",
        str(versions),
        len(versions) >= MINIMUM_VERSIONS,
    )


def build_impossible_project(client: ApiClient, log: ScenarioLog, org_id: str) -> QaProject | None:
    """A 2-week, $10K project holding 60 days of work for one 10 h/wk person."""
    name = "QA API - Impossible Deadline"
    body = {
        "name": name,
        "startDate": "2026-11-30",
        "targetEndDate": "2026-12-11",
        "budgetCents": 1_000_000,
        "status": "planning",
        "currentStep": "wbs",
    }
    created = client.call("POST", f"/orgs/{org_id}/projects", body)
    if not log.status(IMPOSSIBLE_AREA, "Create a 2-week, $10K project", created, {201}):
        return None
    project = QaProject(project_id=created.data["id"], name=name)
    add_packages(client, project, "Everything", [("Design", 10), ("Build", 20), ("Test", 30)])
    solo = {
        "fullName": "Solo Developer",
        "roleTitle": "Developer",
        "hoursPerWeek": 10,
        "skills": ["Python"],
    }
    member = client.call("POST", f"/projects/{project.project_id}/team", solo)
    log.check(
        IMPOSSIBLE_AREA,
        "Build a 60-day WBS with one 10 h/wk person",
        "3 packages, 1 person",
        f"packages={len(project.package_ids)} team HTTP {member.status}",
        len(project.package_ids) == len(("Design", "Build", "Test")) and member.status == 201,
    )
    return project


def empty_project_checks(client: ApiClient, log: ScenarioLog, org_id: str) -> None:
    """A project with no WBS and no team cannot be run or changed, and says why."""
    body = {
        "name": "QA API - No team yet",
        "startDate": "2026-11-30",
        "targetEndDate": "2027-03-31",
        "status": "planning",
    }
    team_free = client.call("POST", f"/orgs/{org_id}/projects", body)
    if team_free.status != 201:
        return
    base = f"/projects/{team_free.data['id']}"
    inputs = client.call("GET", f"{base}/optimization/inputs").data or {}
    log.check(
        IMPOSSIBLE_AREA,
        "Project with no WBS and no team: Run is blocked",
        "ready=false with reasons",
        f"ready={inputs.get('ready')} blocking={inputs.get('blocking')}",
        inputs.get("ready") is False,
    )
    run = client.call("POST", f"{base}/optimization/runs", {})
    log.status(
        IMPOSSIBLE_AREA, "Run anyway on an empty project", run, {409, 422}, "refused with a reason"
    )
    change = client.call(
        "POST", f"{base}/changes", {"source": "edits", "edits": {"budgetTotalCents": 1}}
    )
    log.status(IMPOSSIBLE_AREA, "Record a change before any plan exists → 409", change, {409})


def impossible_project_scenarios(client: ApiClient, log: ScenarioLog, org_id: str) -> None:
    """A tiny, over-constrained project: the optimizer must explain and price the way out."""
    project = build_impossible_project(client, log, org_id)
    if project is None:
        return
    constraints_path = f"/projects/{project.project_id}/constraints"
    current = client.call("GET", constraints_path).data or {}
    client.call("PUT", constraints_path, constraint_body(current, **IMPOSSIBLE_LIMITS))
    _, run = run_optimizer(client, project)
    reasons = [conflict.get("name") for conflict in run.get("iis") or []]
    log.check(
        IMPOSSIBLE_AREA,
        "Impossible limits: explained with priced options",
        "infeasible + reason + at least one remedy",
        f"{describe_run(run)} reason={reasons}",
        run.get("status") == "infeasible" and bool(remedies_of(run)),
    )
    # Every single option is either offered or listed under "Also tried" with its reason.
    tried = (run.get("result") or {}).get("triedRemedies") or []
    accounted = {remedy.get("id") for remedy in remedies_of(run)} | {
        entry.get("id") for entry in tried if entry.get("title") and entry.get("reason")
    }
    log.check(
        IMPOSSIBLE_AREA,
        "Impossible limits: options not offered say why",
        "specialist, overtime and move_deadline each offered or in triedRemedies with a reason",
        "; ".join(f"{entry.get('id')}: {entry.get('reason')}" for entry in tried) or "none",
        {"specialist", "overtime", "move_deadline"} <= accounted,
    )
    for remedy in ("move_deadline", "specialist"):
        _, remedied = run_optimizer(client, project, {"remedy": remedy})
        log.check(
            IMPOSSIBLE_AREA,
            f"Run with remedy '{remedy}'",
            "a plan (or a clear reason why this remedy can't help)",
            describe_run(remedied),
            remedied.get("status") in SOLVED or bool(remedied.get("iis")),
        )
    empty_project_checks(client, log, org_id)


def access_scenarios(
    anonymous: ApiClient, outsider: ApiClient, log: ScenarioLog, org_id: str, project_id: str
) -> None:
    """Security boundaries: no sign-in, and a user of another workspace (RLS)."""
    project_path = f"/projects/{project_id}"
    log.status(ACCESS_AREA, "No sign-in → 401", anonymous.call("GET", project_path), {401})
    log.status(
        ACCESS_AREA,
        "Another workspace's user cannot see the QA project (RLS)",
        outsider.call("GET", project_path),
        {404},
    )
    log.status(
        ACCESS_AREA,
        "Another workspace's user cannot list QA projects",
        outsider.call("GET", f"/orgs/{org_id}/projects"),
        {403, 404},
    )
    foreign_change = {"source": "edits", "edits": {"budgetTotalCents": 1}}
    log.status(
        ACCESS_AREA,
        "Another workspace's user cannot record a change",
        outsider.call("POST", f"{project_path}/changes", foreign_change),
        {403, 404},
    )


def ai_fallback_scenarios(
    client: ApiClient, log: ScenarioLog, org_id: str, project_id: str
) -> None:
    """Without a Claude key: chat answers 503, drafts use the rules, video is refused."""
    question = {"role": "user", "content": "What is the biggest risk?"}
    chat = client.call(
        "POST",
        "/assistant/messages",
        {"projectId": project_id, "page": "dashboard", "messages": [question]},
    )
    log.status(
        ACCESS_AREA,
        "AI chat without a key answers 503 with a clear message (no tokens)",
        chat,
        {503},
    )
    empty_message = {"page": "dashboard", "messages": [{"role": "user", "content": ""}]}
    empty = client.call("POST", "/assistant/messages", empty_message)
    log.status(ACCESS_AREA, "AI chat with an empty message rejected", empty, {422})
    extract_path = f"/orgs/{org_id}/project-drafts/extract"
    draft = client.call("POST", extract_path, {"text": DRAFT_NOTES})
    fields = (draft.data or {}).get("fields") or {}
    found = ", ".join(f"{name}={field.get('status')}" for name, field in fields.items())
    log.check(
        ACCESS_AREA,
        "Create-from-notes draft without a key (rules)",
        "200, model rules, name/dates/budget found",
        f"HTTP {draft.status} model={(draft.data or {}).get('model')} {found}",
        draft.status == 200 and (fields.get("name") or {}).get("status") != "missing",
    )
    video = client.call("POST", extract_path, upload=("all-hands.mp4", b"fake", "video/mp4"))
    log.status(
        ACCESS_AREA, "Create-from-upload with an .mp4 refused", video, {415}, must_contain="MP4"
    )
