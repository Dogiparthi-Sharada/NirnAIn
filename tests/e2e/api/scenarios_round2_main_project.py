"""API end-to-end scenarios for round-2 decisions D1-D3 on the main test project.

Purpose: Prove the decisions with cases that would fail on the old behaviour: worker types
and rates (D1), slips named with priced ways to keep the date (D2) and no plan lock after
Plan v1 (D3). D4 and D6 run on small projects (`scenarios_round2_small_projects.py`).
How it fits: `run_api_suite.py` runs 5e after the team and 7a right after Plan v1;
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from typing import Any

from suite_client import ApiClient
from suite_fixtures import QaProject, constraint_body
from suite_planning import SOLVED, describe_run, remedies_of, run_optimizer, summary_of
from suite_report import ScenarioLog

WORKER_AREA = "5e. Worker type: employee or contractor (D1)"
DIRECT_EDIT_AREA = "7a. After Plan v1: inputs stay editable (D3) and slips are explained (D2)"
EXPECTED_EMPLOYEES = 4


def worker_type_scenarios(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """D1: employees have no rate input; contractors must have one."""
    base = f"/projects/{project.project_id}/team"
    no_rate = {"fullName": "Rate Missing", "roleTitle": "Contractor", "hoursPerWeek": 20}
    log.status(
        WORKER_AREA,
        "Contractor without an hourly rate rejected",
        client.call("POST", base, {**no_rate, "workerType": "contractor"}),
        {422},
        must_contain="rate",
    )
    typed_rate = {"fullName": "Typed Rate", "roleTitle": "Analyst", "hoursPerWeek": 20}
    log.status(
        WORKER_AREA,
        "Employee with a typed rate rejected (rates come from the company record)",
        client.call(
            "POST", base, {**typed_rate, "workerType": "employee", "hourlyRateCents": 9_000}
        ),
        {422},
        must_contain="company record",
    )
    contractor_body = {
        "fullName": "Casey Contractor",
        "roleTitle": "ETL Contractor",
        "hoursPerWeek": 20,
        "workerType": "contractor",
        "hourlyRateCents": 14_000,
        "skills": ["Python"],
    }
    contractor = client.call("POST", base, contractor_body)
    stored = contractor.data or {}
    log.check(
        WORKER_AREA,
        "Contractor with $140/h accepted",
        "201, workerType contractor, rate 14000",
        f"HTTP {contractor.status} type={stored.get('workerType')} "
        f"rate={stored.get('hourlyRateCents')}",
        contractor.status == 201
        and stored.get("workerType") == "contractor"
        and stored.get("hourlyRateCents") == contractor_body["hourlyRateCents"],
    )
    team = client.call("GET", base).data or []
    employees = [member for member in team if member.get("workerType") == "employee"]
    log.check(
        WORKER_AREA,
        "Existing people are employees by default",
        "every original member is an employee",
        f"{len(employees)} employees of {len(team)}",
        len(employees) >= EXPECTED_EMPLOYEES,
    )
    if contractor.status == 201:
        removed = client.call("DELETE", f"{base}/{stored['employeeId']}")
        label = "Remove the contractor again (keeps later scenarios comparable)"
        log.status(WORKER_AREA, label, removed, {204})


def keep_the_date_checks(
    client: ApiClient, log: ScenarioLog, project: QaProject, run: dict[str, Any], label: str
) -> None:
    """D2: a slipping plan names the date it misses and prices every way to keep it."""
    summary = summary_of(run)
    remedies = remedies_of(run)
    offered = {remedy["id"]: remedy for remedy in remedies}
    log.check(
        DIRECT_EDIT_AREA,
        f"{label}: the PM is told the date moves",
        "slipDays > 0 with the target date named",
        f"target={summary.get('targetDate')} finish={summary.get('completionDate')} "
        f"slipDays={summary.get('slipDays')}",
        (summary.get("slipDays") or 0) > 0 and summary.get("targetDate") is not None,
    )
    log.check(
        DIRECT_EDIT_AREA,
        f"{label}: ways to keep the date are offered with date and extra cost",
        "move_deadline plus at least one of contractor / overtime",
        "; ".join(
            f"{remedy['id']}: {remedy.get('completionDate')} "
            f"+${(remedy.get('costCents') or 0) / 100:,.0f}"
            for remedy in remedies
        )
        or "none",
        "move_deadline" in offered and bool({"specialist", "overtime"} & set(offered)),
    )
    # Each priced option must deliver the date it promised when the PM picks it.
    for remedy_id in ("specialist", "overtime"):
        if remedy_id not in offered:
            continue
        _, chosen = run_optimizer(client, project, {"remedy": remedy_id})
        promised = offered[remedy_id].get("completionDate")
        delivered = summary_of(chosen).get("completionDate")
        log.check(
            DIRECT_EDIT_AREA,
            f"{label}: choosing '{remedy_id}' delivers the promised date",
            f"finish {promised}",
            describe_run(chosen),
            chosen.get("status") in SOLVED
            and delivered is not None
            and promised is not None
            and delivered <= promised,
        )


def late_edit_probes(client: ApiClient, log: ScenarioLog, base: str) -> None:
    """D3: constraints, skills and requirements can all still be edited after Plan v1."""
    current = client.call("GET", f"{base}/constraints").data or {}
    saved = client.call("PUT", f"{base}/constraints", constraint_body(current))
    log.status(DIRECT_EDIT_AREA, "Constraints can be saved after Plan v1", saved, {200})
    skill = client.call("POST", f"{base}/skills", {"name": "Late Skill Probe", "required": False})
    log.status(DIRECT_EDIT_AREA, "A skill can be added after Plan v1", skill, {201})
    if skill.status == 201:
        client.call("DELETE", f"{base}/skills/{skill.data['skillId']}")
    item_body = {"category": "requirement", "text": "Late requirement probe"}
    item = client.call("POST", f"{base}/scope-items", item_body)
    log.status(DIRECT_EDIT_AREA, "A requirement can be added after Plan v1", item, {201})
    if item.status == 201:
        client.call("PATCH", f"/scope-items/{item.data['id']}", {"status": "removed"})


def direct_edit_scenarios(
    client: ApiClient, log: ScenarioLog, project: QaProject, applied_run_id: str
) -> None:
    """D3: after Plan v1 the inputs stay editable; a re-run aims for the plan in force's date."""
    base = f"/projects/{project.project_id}"
    applied = client.call("GET", f"{base}/optimization/runs/{applied_run_id}").data or {}
    promised = summary_of(applied).get("completionDate")
    names = list(project.team_ids)
    cut_person = project.team_ids[names[1]]
    team = client.call("GET", f"{base}/team").data or []
    original_hours = next(
        (member["hoursPerWeek"] for member in team if member["employeeId"] == cut_person), 40
    )
    edited = client.call("PATCH", f"{base}/team/{cut_person}", {"hoursPerWeek": 16})
    label = f"Cut {names[1]} to 16 h/wk directly on step 5 after Plan v1"
    log.status(DIRECT_EDIT_AREA, label, edited, {200})
    inputs = client.call("GET", f"{base}/optimization/inputs").data or {}
    log.check(
        DIRECT_EDIT_AREA,
        "Step 6 can tell the inputs changed since Plan v1",
        "hash ≠ appliedHash",
        f"hash={str(inputs.get('hash'))[:8]} appliedHash={str(inputs.get('appliedHash'))[:8]}",
        bool(inputs.get("appliedHash")) and inputs.get("hash") != inputs.get("appliedHash"),
    )
    _, rerun = run_optimizer(client, project)
    target = summary_of(rerun).get("targetDate")
    log.check(
        DIRECT_EDIT_AREA,
        "Re-run aims for the plan in force's date",
        f"targetDate = Plan v1 finish {promised}",
        f"targetDate={target}",
        target == promised,
    )
    keep_the_date_checks(client, log, project, rerun, f"{names[1]} cut to 16 h/wk")
    restored = client.call(
        "PATCH", f"{base}/team/{cut_person}", {"hoursPerWeek": int(original_hours)}
    )
    label = f"Give {names[1]} their {original_hours:g} h/wk back directly"
    log.status(DIRECT_EDIT_AREA, label, restored, {200})
    restored_inputs = client.call("GET", f"{base}/optimization/inputs").data or {}
    same = restored_inputs.get("hash") == restored_inputs.get("appliedHash")
    log.check(
        DIRECT_EDIT_AREA,
        "Back to the plan-in-force inputs",
        "hash = appliedHash",
        f"same={same}",
        same,
    )
    late_edit_probes(client, log, base)
    versions = [
        item.get("version") for item in client.call("GET", f"{base}/plan-versions").data or []
    ]
    log.check(
        DIRECT_EDIT_AREA,
        "Direct edits and re-runs alone create no new version",
        "[1]",
        str(versions),
        versions == [1],
    )
