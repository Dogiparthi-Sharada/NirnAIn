"""API end-to-end scenarios for step 5: the team and the required skills.

Purpose: Exercise the step-5 team editor (add, duplicate, invalid hours, edit, remove,
re-add, owners) and the required-skills editor (add, duplicate, rename and merge,
Required / Optional, remove) with their corner cases.
How it fits: `run_api_suite.py` runs area 5a after the WBS and area 5b after the worker-type
checks (5e); team members are stored in `QaProject.team_ids` for the planning scenarios;
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

from suite_client import ApiClient
from suite_fixtures import TEAM, QaProject
from suite_report import ScenarioLog

TEAM_AREA = "5a. Team & resources"
SKILLS_AREA = "5b. Required skills"
EXPECTED_TEAM_SIZE = 4


def add_team(client: ApiClient, log: ScenarioLog, project: QaProject, base: str) -> None:
    """Adds the fake team, then the duplicate and invalid people that must be refused."""
    for full_name, role, hours, skills in TEAM:
        body = {"fullName": full_name, "roleTitle": role, "hoursPerWeek": hours, "skills": skills}
        added = client.call("POST", base, body)
        log.status(TEAM_AREA, f"Add {full_name} ({role}, {hours} h/wk)", added, {201})
        if added.status == 201:
            project.team_ids[full_name] = added.data["employeeId"]
    duplicate = client.call(
        "POST", base, {"fullName": "priya RAMAN", "roleTitle": "Data Architect", "hoursPerWeek": 10}
    )
    log.status(TEAM_AREA, "Add the same person again (different case) → 409", duplicate, {409})
    for hours in (0, 81, 1.5):
        invalid = {"fullName": "Invalid Hours", "roleTitle": "Tester", "hoursPerWeek": hours}
        answer = client.call("POST", base, invalid)
        log.status(TEAM_AREA, f"Hours per week = {hours} rejected", answer, {422})
    no_role = client.call(
        "POST", base, {"fullName": "No Role", "roleTitle": "", "hoursPerWeek": 10}
    )
    log.status(TEAM_AREA, "Missing role rejected", no_role, {422})


def edit_team(client: ApiClient, log: ScenarioLog, project: QaProject, base: str) -> None:
    """Changes Leo's hours, removes and re-adds Sam, and edits someone not on the team."""
    leo, sam = project.team_ids.get("Leo Martins"), project.team_ids.get("Sam Okafor")
    if leo:
        cut = client.call(
            "PATCH", f"{base}/{leo}", {"hoursPerWeek": 32, "skills": ["Python", "Spark", "Kafka"]}
        )
        log.status(TEAM_AREA, "Decrease Leo's hours 40 → 32 and add a skill", cut, {200})
        zero = client.call("PATCH", f"{base}/{leo}", {"hoursPerWeek": 0})
        log.status(TEAM_AREA, "Edit hours to 0 rejected", zero, {422})
    if sam:
        log.status(
            TEAM_AREA, "Remove Sam from the team", client.call("DELETE", f"{base}/{sam}"), {204}
        )
        again = client.call("DELETE", f"{base}/{sam}")
        log.status(TEAM_AREA, "Remove Sam again → 404", again, {404})
        readd_body = {
            "fullName": "Sam Okafor",
            "roleTitle": "DevOps Engineer",
            "hoursPerWeek": 16,
            "skills": ["Kubernetes"],
        }
        readded = client.call("POST", base, readd_body)
        same_person = (readded.data or {}).get("employeeId") == sam
        log.check(
            TEAM_AREA,
            "Re-add Sam with fewer hours",
            "201, same org person reused",
            f"HTTP {readded.status} same id={same_person}",
            readded.status == 201,
        )
        if readded.status == 201:
            project.team_ids["Sam Okafor"] = readded.data["employeeId"]
    stranger = client.call("PATCH", f"{base}/{uuid.uuid4()}", {"hoursPerWeek": 10})
    log.status(TEAM_AREA, "Edit a person not on the team → 404", stranger, {404})


def team_scenarios(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """Step 5 team: add, duplicate, invalid hours, edit, remove, re-add, then owners."""
    base = f"/projects/{project.project_id}/team"
    add_team(client, log, project, base)
    edit_team(client, log, project, base)
    team = client.call("GET", base).data or []
    log.check(
        TEAM_AREA,
        "Team list shows hours, booked hours and skills",
        "4 people",
        "; ".join(
            f"{member['fullName']} {member['hoursPerWeek']}h booked {member.get('bookedHours')}"
            for member in team
        ),
        len(team) == EXPECTED_TEAM_SIZE,
    )
    for path in ("capacity", "skills-coverage"):
        answer = client.call("GET", f"/projects/{project.project_id}/{path}")
        log.status(TEAM_AREA, f"GET {path}", answer, {200})
    owners = list(project.team_ids.values())
    assigned = [
        client.call(
            "PATCH", f"/wbs/nodes/{node_id}", {"ownerEmployeeId": owners[index % len(owners)]}
        ).status
        for index, node_id in enumerate(project.package_ids)
    ]
    log.check(
        TEAM_AREA,
        "Assign an owner to every work package",
        "all 200",
        f"{assigned.count(200)}/{len(assigned)}",
        bool(assigned) and all(code == 200 for code in assigned),
    )


def rename_scenarios(client: ApiClient, log: ScenarioLog, base: str, skill_id: str) -> None:
    """Renames a skill (possibly merging it into a workspace skill) and flips Required."""
    renamed = client.call("PATCH", f"{base}/{skill_id}", {"name": "Security & Compliance Review"})
    log.status(SKILLS_AREA, "Rename the skill", renamed, {200})
    current_id = (renamed.data or {}).get("skillId") or skill_id
    log.check(
        SKILLS_AREA,
        "Rename to a name another project already uses merges into that workspace skill",
        "200; the returned id is used from now on",
        f"id {'changed (merged)' if current_id != skill_id else 'kept'}",
        renamed.status == 200,
    )
    skill_path = f"{base}/{current_id}"
    optional = client.call("PATCH", skill_path, {"required": False})
    log.status(SKILLS_AREA, "Switch Required → Optional", optional, {200})
    required = client.call("PATCH", skill_path, {"required": True})
    log.status(SKILLS_AREA, "Switch back to Required", required, {200})
    sibling = client.call("POST", base, {"name": "Clash Probe", "required": False})
    if sibling.status == 201:
        sibling_path = f"{base}/{sibling.data['skillId']}"
        clash = client.call("PATCH", sibling_path, {"name": "security & compliance review"})
        log.status(
            SKILLS_AREA,
            "Rename to a name already in this project's list rejected",
            clash,
            {409, 422},
        )
        client.call("DELETE", sibling_path)


def skills_scenarios(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """Step 5 skills: add, duplicate, rename, required/optional, remove."""
    base = f"/projects/{project.project_id}/skills"
    existing = client.call("GET", base).data or []
    listing = ", ".join(
        f"{skill['name']}({'req' if skill['required'] else 'opt'}, {len(skill['holders'])} holders)"
        for skill in existing[:8]
    )
    log.check(
        SKILLS_AREA, "Skill list with holders", "list", f"{len(existing)} skills: {listing}", True
    )
    added = client.call("POST", base, {"name": "Security Review", "required": True})
    log.status(SKILLS_AREA, "Add required skill 'Security Review' (nobody has it)", added, {201})
    duplicate = client.call("POST", base, {"name": "security review", "required": False})
    log.status(SKILLS_AREA, "Add duplicate 'security review' → 409", duplicate, {409})
    empty = client.call("POST", base, {"name": "", "required": True})
    log.status(SKILLS_AREA, "Empty skill name rejected", empty, {422})
    if added.status == 201:
        rename_scenarios(client, log, base, added.data["skillId"])
    temporary = client.call("POST", base, {"name": "Temporary Skill", "required": False})
    if temporary.status == 201:
        temporary_path = f"{base}/{temporary.data['skillId']}"
        log.status(SKILLS_AREA, "Remove a skill", client.call("DELETE", temporary_path), {204})
        log.status(
            SKILLS_AREA, "Remove it again → 404", client.call("DELETE", temporary_path), {404}
        )
