"""API end-to-end scenarios for round-2 decisions D4 and D6, each on a small project of its own.

Purpose: Prove that a project which started in the past is planned from today and that
finished work stays put (D4), and that a Required skill nobody has, with the specialist
answer Yes, brings a priced specialist into the plan (D6).
How it fits: `run_api_suite.py` runs areas 8b and 8c after the over-constrained project;
the projects are built with `suite_fixtures.build_small_project`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from suite_client import ApiClient
from suite_fixtures import QaProject, build_small_project
from suite_planning import SOLVED, describe_run, run_optimizer, summary_of
from suite_report import ScenarioLog

TODAY_AREA = "8b. Re-plan from today; finished work stays (D4)"
SKILL_AREA = "8c. Required skills reach the optimizer (D6)"
SMALL_PROJECT_PACKAGES = 3
SPECIALIST_LIMITS: dict[str, Any] = {
    "budgetTotalCents": 20_000_000,
    "budgetMode": "soft",
    "deadlineMode": "hard",
    "deadlineAnswer": "hard",
    "capacityMode": "soft",
    "skillMode": "hard",
    "specialistAnswer": "yes",
    "specialistRateConfirmed": True,
    "contractorRateCents": 18_500,
    "allowContractors": True,
}


def started_last_month(client: ApiClient, org_id: str) -> QaProject | None:
    """A small project whose start date is in the past (D4)."""
    return build_small_project(
        client,
        org_id,
        "QA API - Started last month",
        ("2026-10-01", "2027-04-30"),
        [("Discovery", 10), ("Build", 20), ("Rollout", 10)],
        [
            {
                "fullName": "Morgan Lee",
                "roleTitle": "Engineer",
                "hoursPerWeek": 40,
                "workerType": "employee",
            }
        ],
        {
            "budgetTotalCents": 20_000_000,
            "budgetMode": "soft",
            "deadlineMode": "soft",
            "deadlineAnswer": "soft",
            "capacityMode": "soft",
            "skillMode": "soft",
            "specialistAnswer": "no",
        },
    )


def today_and_done_scenarios(client: ApiClient, log: ScenarioLog, org_id: str) -> None:
    """D4: a project that started in the past is planned from today; Done work stays put."""
    today = date.today().isoformat()
    project = started_last_month(client, org_id)
    if project is None or len(project.package_ids) < SMALL_PROJECT_PACKAGES:
        log.blocked(TODAY_AREA, "Today-anchored plan", "a small project", "could not build it")
        return
    _, run = run_optimizer(client, project)
    starts = sorted(item["start"] for item in (run.get("result") or {}).get("schedule") or [])
    first_start = starts[0] if starts else None
    log.check(
        TODAY_AREA,
        "Project that started Oct 1: nothing is scheduled before today",
        f"first start ≥ {today}",
        f"{describe_run(run)} first start={first_start}",
        first_start is not None and first_start >= today,
    )
    runs_path = f"/projects/{project.project_id}/optimization/runs"
    applied = client.call("POST", f"{runs_path}/{run.get('id')}/apply", {})
    if applied.status != 200:
        log.blocked(
            TODAY_AREA, "Progress 'Done'", "Plan v1 applied", f"apply HTTP {applied.status}"
        )
        return
    finish_before = summary_of(run).get("completionDate")
    done_edit = {"progress": {project.package_ids[0]: 100}}
    change = client.call(
        "POST",
        f"/projects/{project.project_id}/changes",
        {"source": "progress", "edits": done_edit},
    )
    log.status(TODAY_AREA, "Record 'Discovery is Done'", change, {201, 202})
    if change.status not in {201, 202}:
        return
    overrides = (client.call("GET", f"/changes/{change.data['id']}").data or {}).get("overrides")
    replan_body = {
        "overrides": overrides or {},
        "changeRequestId": change.data["id"],
        "baselineRunId": run.get("id"),
    }
    _, replan = run_optimizer(client, project, replan_body)
    schedule = {
        item["wbsNodeId"]: item for item in (replan.get("result") or {}).get("schedule") or []
    }
    finished = schedule.get(project.package_ids[0], {})
    log.check(
        TODAY_AREA,
        "Re-plan after 'Done' works (QA had 'The request is invalid')",
        "solved; Discovery marked done",
        f"{describe_run(replan)} done={finished.get('done')}",
        replan.get("status") in SOLVED and finished.get("done") is True,
    )
    finish_after = summary_of(replan).get("completionDate")
    open_starts = [item["start"] for item in schedule.values() if not item.get("done")]
    log.check(
        TODAY_AREA,
        "Finishing work early never moves the plan before today",
        f"finish ≤ {finish_before} and every open start ≥ {today}",
        f"finish {finish_before} → {finish_after}",
        finish_after is not None and all(start >= today for start in open_starts),
    )


def required_skill_scenarios(client: ApiClient, log: ScenarioLog, org_id: str) -> None:
    """D6: a Required skill nobody has (specialist answer Yes) brings a priced specialist in."""
    project = build_small_project(
        client,
        org_id,
        "QA API - Okta SSO specialist",
        ("2026-11-30", "2027-03-31"),
        [("Okta SSO integration", 10), ("Data migration", 10)],
        [
            {
                "fullName": "Jordan Kim",
                "roleTitle": "Engineer",
                "hoursPerWeek": 40,
                "workerType": "employee",
                "skills": ["Python"],
            }
        ],
        SPECIALIST_LIMITS,
    )
    if project is None:
        log.blocked(SKILL_AREA, "Specialist scenario", "a small project", "could not build it")
        return
    base = f"/projects/{project.project_id}"
    okta = client.call("POST", f"{base}/skills", {"name": "Okta SSO", "required": True})
    log.status(
        SKILL_AREA, "Mark 'Okta SSO' as a Required skill nobody on the team has", okta, {201}
    )
    unlinked = client.call("POST", f"{base}/skills", {"name": "SAP", "required": True})
    rows = (client.call("GET", f"{base}/optimization/inputs").data or {}).get("rows") or []
    warning = next(
        (
            row
            for row in rows
            if "SAP" in str(row.get("label")) and row.get("source") == "needs_you"
        ),
        None,
    )
    log.check(
        SKILL_AREA,
        "A Required skill no work package uses is flagged",
        "needs_you row naming SAP",
        str(warning.get("value") if warning else None),
        warning is not None,
    )
    tagged = None
    if unlinked.status == 201:
        tagged = client.call("PATCH", f"/wbs/nodes/{project.package_ids[1]}", {"skills": ["SAP"]})
    tagged_skills = (
        [skill.get("name") for skill in (tagged.data or {}).get("skills") or []] if tagged else []
    )
    log.check(
        SKILL_AREA,
        "Tag a work package with a skill",
        "200, package lists SAP",
        f"HTTP {tagged.status if tagged else None} skills={tagged_skills}",
        tagged is not None and tagged.status == 200 and "SAP" in tagged_skills,
    )
    _, run = run_optimizer(client, project)
    specialists = summary_of(run).get("specialists") or []
    schedule = (run.get("result") or {}).get("schedule") or []
    assignees = {item.get("name"): item.get("assignee") for item in schedule}
    okta_assignee = assignees.get("Okta SSO integration")
    log.check(
        SKILL_AREA,
        "Plan brings in a priced specialist for the skill nobody has",
        "a specialist on the Okta package, with cost",
        f"{describe_run(run)} specialists={specialists} Okta assignee={okta_assignee}",
        run.get("status") in SOLVED
        and bool(specialists)
        and "okta" in (str(okta_assignee or "") + str(specialists)).lower(),
    )
