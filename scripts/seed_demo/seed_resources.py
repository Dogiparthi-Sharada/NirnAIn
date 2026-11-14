"""Steps 4-5 of the demo seed: WBS, team, skills, dependencies, validation and limits.

Purpose: Build the WBS with owners, the team (employees and contractors), the required skills
and work-package skill tags, the dependencies and the step-5 limits and answers through the
endpoints the screens call; run "Validate WBS" only when the API answers with its built-in
rules (no Claude key).
How it fits: `seed_flows.Seeder.seed` calls these functions, in the order a project manager
works, after scope review; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from typing import Any

from seed_client import ApiClient, SeedError, wait_until
from seed_model import ProjectSpec

LOCAL_MODELS = {"rules", "template"}
JOB_DONE = {"succeeded", "failed", "cancelled"}


def add_team(api: ApiClient, project_id: str, spec: ProjectSpec) -> dict[str, str]:
    """Step 5 team (added first so the WBS can name owners). Returns name → employee id."""
    ids: dict[str, str] = {}
    for person in spec.team:
        body: dict[str, Any] = {
            "fullName": person.name,
            "roleTitle": person.role,
            "hoursPerWeek": person.hours_per_week,
            "skills": list(person.skills),
            "workerType": "contractor" if person.contractor_rate_cents else "employee",
        }
        if person.contractor_rate_cents:
            body["hourlyRateCents"] = person.contractor_rate_cents
        member = api.expect(
            "POST", f"/projects/{project_id}/team", body, step=f"Step 5 add {person.name}"
        )
        ids[person.name] = str(member["employeeId"])
    return ids


def build_wbs(
    api: ApiClient, project_id: str, spec: ProjectSpec, team_ids: dict[str, str]
) -> dict[str, str]:
    """Step 4: root, deliverables and work packages with owners. Returns mockup code → node id."""
    path = f"/projects/{project_id}/wbs/nodes"
    root = api.expect("POST", path, {"parentId": None, "name": spec.name}, step="Step 4 WBS root")
    ids: dict[str, str] = {}
    for deliverable in spec.deliverables:
        node = api.expect(
            "POST",
            path,
            {"parentId": root["id"], "name": deliverable.name},
            step="Step 4 deliverable",
        )
        ids[deliverable.code] = str(node["id"])
        for package in deliverable.packages:
            body = {
                "parentId": node["id"],
                "name": package.name,
                "durationDays": package.duration_days,
                "ownerEmployeeId": team_ids.get(package.owner or ""),
            }
            ids[package.code] = str(
                api.expect("POST", path, body, step=f"Step 4 package {package.code}")["id"]
            )
    api.expect(
        "POST",
        f"/projects/{project_id}/wbs/confirm-estimates",
        {},
        step="Step 4 confirm estimates",
    )
    return ids


def set_skills(
    api: ApiClient, project_id: str, spec: ProjectSpec, node_ids: dict[str, str]
) -> None:
    """Step 5 required skills, then each work package tagged with the skills it needs (step 4)."""
    path = f"/projects/{project_id}/skills"
    listed = {
        skill["name"].casefold(): skill
        for skill in api.expect("GET", path, step="Step 5 skills") or []
    }
    for name, required in spec.skills:
        current = listed.get(name.casefold())
        if current is None:
            api.expect(
                "POST", path, {"name": name, "required": required}, step=f"Step 5 skill {name}"
            )
        elif current["required"] != required:
            api.expect(
                "PATCH",
                f"{path}/{current['skillId']}",
                {"required": required},
                step=f"Step 5 skill {name}",
            )
    for package in spec.packages:
        if package.skills:
            api.expect(
                "PATCH",
                f"/wbs/nodes/{node_ids[package.code]}",
                {"skills": list(package.skills)},
                step=f"Step 4 tag {package.code}",
            )


def link(api: ApiClient, project_id: str, spec: ProjectSpec, node_ids: dict[str, str]) -> None:
    """Step 5 task dependencies."""
    for link in spec.links:
        body = {
            "predecessorId": node_ids[link.predecessor],
            "successorId": node_ids[link.successor],
            "type": link.kind,
            "lagDays": 0,
        }
        api.expect(
            "POST",
            f"/projects/{project_id}/dependencies",
            body,
            step=f"Step 5 dependency {link.predecessor}→{link.successor}",
        )


def validate_wbs(api: ApiClient, project_id: str) -> None:
    """Step 4 'Validate WBS' (rules): its issues become the dashboard's AI findings."""
    started = api.expect("POST", f"/projects/{project_id}/wbs/validate", step="Step 4 validate")
    job = wait_until(
        lambda: api.expect(
            "GET",
            f"/projects/{project_id}/ai-jobs/latest",
            query={"kind": "wbs_validate"},
            step="Step 4 validate",
        ),
        lambda latest: (
            latest.get("id") == started.get("id", latest.get("id"))
            and latest.get("status") in JOB_DONE
        ),
        step="Step 4 validate",
    )
    if job.get("model") not in LOCAL_MODELS:
        raise SeedError(
            f"Step 4 validation ran with '{job.get('model')}': this API has an AI key. "
            "Use --ai-calls skip."
        )


def set_limits(
    api: ApiClient, project_id: str, spec: ProjectSpec, node_ids: dict[str, str]
) -> None:
    """Step 5 limits, Must hold / Can flex choices, milestones and the two questions."""
    limits = spec.limits
    if limits is None:
        raise SeedError(f"Step 5 constraints: '{spec.name}' has no limits to save")
    body = {
        "budgetTotalCents": limits.budget_cents,
        "contingencyPct": limits.contingency_pct,
        "laborCapCents": limits.labor_cap_cents,
        "infraCapCents": limits.infra_cap_cents,
        "budgetMode": limits.budget_mode,
        "startDate": limits.start.isoformat(),
        "mustFinishBy": limits.must_finish_by.isoformat(),
        "deadlineMode": "soft" if limits.deadline_answer == "soft" else "hard",
        "workingDaysPerWeek": 5,
        "capacityMode": limits.capacity_mode,
        "skillMode": limits.skill_mode,
        "specialistAnswer": limits.specialist_answer,
        "deadlineAnswer": limits.deadline_answer,
        "specialistRateConfirmed": limits.contractor_rate_cents is not None,
        "contractorRateCents": limits.contractor_rate_cents,
        "milestones": [
            {
                "name": milestone.name,
                "dueDate": milestone.due.isoformat(),
                "wbsNodeId": node_ids.get(milestone.deliverable or ""),
                "mode": "hard" if milestone.hard else "soft",
            }
            for milestone in limits.milestones
        ],
    }
    api.expect("PUT", f"/projects/{project_id}/constraints", body, step="Step 5 constraints")
