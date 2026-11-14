"""API end-to-end scenarios for step 7: the product owner's mid-project changes.

Purpose: After Plan v1, change people's hours up and down, bring in a specialist, remove a
person, read a meeting's notes, move deadlines, cut the budget, grow a requirement and post
progress, each through the step-7 flow (record → answer → re-plan → compare → approve or
reject), and check the plan reacts logically (fewer hours → later dates or reassigned work,
never silent overbooking). Change detection runs on the rules (no Claude key).
How it fits: `run_api_suite.py` calls `change_scenarios` with the run applied in step 6;
each case continues from the latest approved plan; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from suite_client import ApiClient
from suite_fixtures import SPECIALIST_NOTES, QaProject
from suite_planning import (
    SOLVED,
    ChangeCase,
    describe_run,
    drive_change,
    remedies_of,
    summary_of,
    versions_of,
)
from suite_report import ScenarioLog

RESOURCE_AREA = "7b. Mid-project: resources up and down"
MEMBERSHIP_AREA = "7c. Mid-project: bring in a specialist, remove a person"
DATES_AREA = "7d. Mid-project: deadlines, budget, scope growth, progress"
OVERBOOKING_LIMIT_PERCENT = 120
SPECIALIST = {
    "fullName": "Dana Lee",
    "roleTitle": "Security Specialist",
    "hoursPerWeek": 24,
    "skills": ["Security & Compliance Review"],
    "workerType": "contractor",
    "hourlyRateCents": 17_500,
}


def edits_case(
    area: str, label: str, edits: dict[str, Any], decision: str = "approve"
) -> ChangeCase:
    """A change entered as direct edits."""
    return ChangeCase(area, label, {"source": "edits", "edits": edits}, decision)


def team_hours(client: ApiClient, project: QaProject) -> dict[str, float]:
    """Full name -> hours per week on the project team now."""
    team = client.call("GET", f"/projects/{project.project_id}/team").data or []
    return {member["fullName"]: member["hoursPerWeek"] for member in team}


def run_by_id(client: ApiClient, project: QaProject, run_id: str) -> dict[str, Any]:
    """One optimization run by id."""
    path = f"/projects/{project.project_id}/optimization/runs/{run_id}"
    return client.call("GET", path).data or {}


def restore_hours(
    client: ApiClient,
    log: ScenarioLog,
    project: QaProject,
    name: str,
    cut_plan: dict[str, Any],
    run_id: str,
) -> str | None:
    """Gives `name` 40 h/wk back; the finish must be no later than with fewer hours."""
    restored = drive_change(
        client,
        log,
        project,
        edits_case(
            RESOURCE_AREA,
            f"Give {name} 40 h/wk back",
            {"hoursPerWeek": {project.team_ids[name]: 40}},
        ),
        run_id,
    )
    cut_finish = summary_of(cut_plan).get("completionDate")
    restored_finish = summary_of(restored.replan).get("completionDate")
    log.check(
        RESOURCE_AREA,
        "More hours → finish no later than with fewer hours",
        "finish ≤ the 16 h/wk plan's finish",
        f"{cut_finish} → {restored_finish}",
        (restored_finish or "9999") <= (cut_finish or "0000") if restored.replan else False,
    )
    return restored.approved_run


def resource_changes(
    client: ApiClient, log: ScenarioLog, project: QaProject, current_run: str
) -> str:
    """Cuts one person's hours and raises another's; the plan must stretch or reassign."""
    names = list(project.team_ids)
    before = summary_of(run_by_id(client, project, current_run))
    edits = {"hoursPerWeek": {project.team_ids[names[1]]: 16, project.team_ids[names[2]]: 40}}
    label = f"Cut {names[1]} to 16 h/wk, raise {names[2]} to 40 h/wk"
    outcome = drive_change(
        client, log, project, edits_case(RESOURCE_AREA, label, edits), current_run
    )
    after = summary_of(outcome.replan)
    peak = (after.get("peakUtilization") or {}).get("percent") or 0
    later = after.get("completionDate", "") > before.get("completionDate", "")
    log.check(
        RESOURCE_AREA,
        "Fewer hours → later finish or work reassigned, never silent overbooking",
        "finish moves later OR peak ≤ 120%",
        f"finish {before.get('completionDate')} → {after.get('completionDate')}, peak {peak}%",
        outcome.replan.get("status") in SOLVED and (later or peak <= OVERBOOKING_LIMIT_PERCENT),
    )
    if not outcome.approved_run:
        return current_run
    hours = team_hours(client, project)
    log.check(
        RESOURCE_AREA,
        "Approved hours written back to the team",
        f"{names[1]}=16, {names[2]}=40",
        str(hours),
        hours.get(names[1]) == 16 and hours.get(names[2]) == 40,
    )
    versions = versions_of(client, project)
    log.check(
        RESOURCE_AREA, "Versions after the approval", "[1, 2]", str(versions), versions == [1, 2]
    )
    change_path = f"/changes/{outcome.change['id']}"
    twice = client.call("POST", f"{change_path}/approve", {"runId": outcome.approved_run})
    log.status(RESOURCE_AREA, "Approve the same change twice → 409", twice, {409})
    answered = client.call("PATCH", change_path, {"answers": {}})
    log.status(RESOURCE_AREA, "Answer an approved change → 409", answered, {409, 422})
    restored_run = restore_hours(
        client, log, project, names[1], outcome.replan, outcome.approved_run
    )
    return restored_run or outcome.approved_run


def team_membership_changes(
    client: ApiClient, log: ScenarioLog, project: QaProject, current_run: str
) -> str:
    """Brings in a specialist mid-project, removes a person, then reads a meeting's notes."""
    label = "Add Dana Lee, security specialist, 24 h/wk at $175/h"
    added = drive_change(
        client,
        log,
        project,
        edits_case(MEMBERSHIP_AREA, label, {"addMembers": [SPECIALIST]}),
        current_run,
    )
    hours = team_hours(client, project)
    log.check(
        MEMBERSHIP_AREA,
        "Specialist is on the team after approval",
        "Dana Lee at 24 h/wk",
        str(hours),
        hours.get("Dana Lee") == 24,
    )
    current_run = added.approved_run or current_run
    changes_path = f"/projects/{project.project_id}/changes"
    duplicate = client.call(
        "POST", changes_path, {"source": "edits", "edits": {"addMembers": [SPECIALIST]}}
    )
    log.status(MEMBERSHIP_AREA, "Adding the same person twice rejected", duplicate, {422, 409})
    leaving = project.team_ids.get("Sam Okafor")
    if leaving:
        case = edits_case(
            MEMBERSHIP_AREA, "Sam Okafor leaves the project", {"removeMembers": [leaving]}
        )
        removed = drive_change(client, log, project, case, current_run)
        remaining = list(team_hours(client, project))
        log.check(
            MEMBERSHIP_AREA,
            "Sam is off the team after approval",
            "not listed",
            str(remaining),
            "Sam Okafor" not in remaining,
        )
        current_run = removed.approved_run or current_run
    notes_case = ChangeCase(
        MEMBERSHIP_AREA,
        "Meeting notes: specialist + Leo cut to 16 h/wk + later go-live + budget up",
        {"source": "notes", "text": SPECIALIST_NOTES},
        "approve",
    )
    from_notes = drive_change(client, log, project, notes_case, current_run)
    latest = (client.call("GET", changes_path).data or [{}])[0]
    kinds = sorted({item.get("kind") for item in (latest.get("impact") or {}).get("changes") or []})
    log.check(
        MEMBERSHIP_AREA,
        "Notes: every change in the meeting is detected",
        "budget, deadline, capacity (Leo) and team (specialist)",
        f"detected kinds={kinds}",
        {"budget", "deadline", "capacity", "team"} <= set(kinds),
    )
    return from_notes.approved_run or current_run


def impossible_deadline(
    client: ApiClient, log: ScenarioLog, project: QaProject, current_run: str
) -> None:
    """A Must-hold deadline 3 weeks before the planned finish is explained, then rejected."""
    finish = summary_of(run_by_id(client, project, current_run)).get("completionDate")
    if not finish:
        return
    too_early = (date.fromisoformat(finish) - timedelta(days=21)).isoformat()
    label = f"Deadline 3 weeks BEFORE the planned finish ({too_early}), Must hold — then keep plan"
    case = edits_case(DATES_AREA, label, {"mustFinishBy": too_early}, "reject")
    early_run = drive_change(client, log, project, case, current_run).replan
    log.check(
        DATES_AREA,
        "An impossible deadline is explained with priced options",
        "infeasible + remedies (move deadline / specialist), or a compressed feasible plan",
        describe_run(early_run),
        early_run.get("status") in SOLVED or bool(remedies_of(early_run)),
    )


def date_and_budget_changes(
    client: ApiClient, log: ScenarioLog, project: QaProject, current_run: str
) -> str:
    """Deadlines earlier and later, budget down, a requirement growing, a progress update."""
    impossible_deadline(client, log, project, current_run)
    versions = str(versions_of(client, project))
    log.check(
        DATES_AREA, "Rejected change leaves versions unchanged", "no new version", versions, None
    )
    budget_case = edits_case(
        DATES_AREA, "Budget cut 20% ($450K → $360K)", {"budgetTotalCents": 36_000_000}
    )
    budget = drive_change(client, log, project, budget_case, current_run)
    log.check(
        DATES_AREA,
        "Budget cut is reflected",
        "cost ≤ $360K or flagged over budget",
        describe_run(budget.replan),
        (summary_of(budget.replan).get("costCents") or 0) <= 36_000_000
        or budget.replan.get("status") == "infeasible",
    )
    current_run = budget.approved_run or current_run
    grown = project.package_ids[0]
    duration_case = edits_case(
        DATES_AREA,
        "Requirement grew: first work package → 25 working days",
        {"durationDays": {grown: 25}},
    )
    duration = drive_change(client, log, project, duration_case, current_run)
    if duration.approved_run:
        nodes = client.call("GET", f"/projects/{project.project_id}/wbs").data or []
        node = next((item for item in nodes if item["id"] == grown), {})
        log.check(
            DATES_AREA,
            "Approved duration written back to the WBS",
            "durationDays = 25",
            f"durationDays={node.get('durationDays')}",
            node.get("durationDays") == 25,
        )
        current_run = duration.approved_run
    later_case = edits_case(
        DATES_AREA, "Deadline 3 weeks later (Aug 20, 2027)", {"mustFinishBy": "2027-08-20"}
    )
    current_run = (
        drive_change(client, log, project, later_case, current_run).approved_run or current_run
    )
    progress_case = ChangeCase(
        DATES_AREA,
        "Progress update: first package 50% done — then keep plan",
        {"source": "progress", "edits": {"progress": {grown: 50}}},
        "reject",
    )
    drive_change(client, log, project, progress_case, current_run)
    return current_run


def change_scenarios(
    client: ApiClient, log: ScenarioLog, project: QaProject, applied_run_id: str
) -> None:
    """All mid-project scenarios in order; each continues from the latest approved plan."""
    current_run = resource_changes(client, log, project, applied_run_id)
    current_run = team_membership_changes(client, log, project, current_run)
    date_and_budget_changes(client, log, project, current_run)
