"""Fake project data and project builders shared by the API end-to-end scenarios.

Purpose: Keep the made-up meeting notes, transcript, team and the helpers that build a
ready-to-plan project in one place, so every scenario module uses the same fake data.
How it fits: the scenario modules import `QaProject`, the texts and `constraint_body`;
the round-2 and corner-case scenarios build small projects with `build_small_project`;
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from suite_client import ApiClient

MEETING_NOTES = """Kickoff meeting - Data Platform Revamp
Jane Doe: The goal of this project is to move all finance and sales reporting onto one data \
platform.
Raj Patel: We need to migrate the sales and finance data first, and the CFO wants the dashboards \
by end of Q2.
Jane Doe: Budget is capped at $450,000 and we only have four people for this.
Maria Lopez: My concern is the legacy ERP export, it could delay the migration.
Raj Patel: We must support single sign-on and role-based access for every dashboard.
Jane Doe: We will deliver a pilot finance dashboard within eight weeks.
Raj Patel: We don't know yet who owns data quality sign-off.
"""

ZOOM_TRANSCRIPT = b"""WEBVTT

1
00:00:01.000 --> 00:00:05.000
Jane Doe: Thanks for joining. The goal is one trusted source for all our

2
00:00:05.000 --> 00:00:09.000
Jane Doe: finance reporting.

3
00:00:09.000 --> 00:00:14.000
Raj Patel: We need to build an automated data quality check before go-live.

4
00:00:14.000 --> 00:00:18.000
Maria Lopez: The risk is that the vendor API changes could slip the schedule.
"""

SPECIALIST_NOTES = (
    "Steering committee, Jan 15. Jane Doe: We agreed to bring in an outside security "
    "specialist for the compliance review. Raj Patel: Leo can only give 16 hours a week from "
    "February. Jane Doe: The go-live can move to July 31, 2027 if needed, and the budget goes "
    "up to $500,000."
)

# (full name, role, hours per week, skills) of the main project's team.
TEAM = [
    ("Priya Raman", "Data Architect", 40, ["Data Modeling", "PostgreSQL"]),
    ("Leo Martins", "Data Engineer", 40, ["Python", "Spark"]),
    ("Ana Souza", "BI Developer", 24, ["Power BI", "SQL"]),
    ("Sam Okafor", "DevOps Engineer", 20, ["Kubernetes", "Terraform"]),
]

CONSTRAINT_KEYS = (
    "budgetTotalCents",
    "contingencyPct",
    "laborCapCents",
    "infraCapCents",
    "budgetMode",
    "startDate",
    "mustFinishBy",
    "deadlineMode",
    "workingDaysPerWeek",
    "capacityMode",
    "skillMode",
    "specialistAnswer",
    "deadlineAnswer",
    "specialistRateConfirmed",
    "contractorRateCents",
    "allowContractors",
)
MILESTONE_KEYS = ("id", "name", "dueDate", "wbsNodeId", "mode")


@dataclass
class QaProject:
    """The IDs later scenarios need about one test project."""

    project_id: str
    name: str
    team_ids: dict[str, str] = field(default_factory=dict)  # full name -> employee id
    package_ids: list[str] = field(default_factory=list)  # level-3 WBS nodes
    root_id: str | None = None


def constraint_body(current: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A full ConstraintSetInput: the GET /constraints answer plus `changes`."""
    body = {key: current.get(key) for key in CONSTRAINT_KEYS}
    body["milestones"] = [
        {key: milestone.get(key) for key in MILESTONE_KEYS}
        for milestone in current.get("milestones") or []
    ]
    if body["contingencyPct"] is None:
        body["contingencyPct"] = 10
    body["workingDaysPerWeek"] = body["workingDaysPerWeek"] or 5
    body.update(changes)
    return body


def add_packages(
    client: ApiClient, project: QaProject, deliverable_name: str, packages: list[tuple[str, int]]
) -> None:
    """Creates a root, one deliverable and the given (name, days) work packages."""
    base = f"/projects/{project.project_id}/wbs/nodes"
    root = client.call("POST", base, {"parentId": None, "name": project.name}).data or {}
    deliverable_body = {"parentId": root.get("id"), "name": deliverable_name}
    deliverable = client.call("POST", base, deliverable_body).data or {}
    for package_name, days in packages:
        package_body = {
            "parentId": deliverable.get("id"),
            "name": package_name,
            "durationDays": days,
        }
        package = client.call("POST", base, package_body)
        if package.status == 201:
            project.package_ids.append(package.data["id"])


def build_small_project(
    client: ApiClient,
    org_id: str,
    name: str,
    dates: tuple[str, str],
    packages: list[tuple[str, int]],
    people: list[dict[str, Any]],
    constraints: dict[str, Any],
) -> QaProject | None:
    """A minimal planned project: one deliverable, the packages and team, saved constraints."""
    start, finish = dates
    body = {
        "name": name,
        "startDate": start,
        "targetEndDate": finish,
        "budgetCents": 20_000_000,
        "status": "planning",
        "currentStep": "constraints",
    }
    created = client.call("POST", f"/orgs/{org_id}/projects", body)
    if created.status != 201:
        return None
    project = QaProject(project_id=created.data["id"], name=name)
    base = f"/projects/{project.project_id}"
    add_packages(client, project, "Delivery", packages)
    for person in people:
        added = client.call("POST", f"{base}/team", person)
        if added.status == 201:
            project.team_ids[person["fullName"]] = added.data["employeeId"]
    current = client.call("GET", f"{base}/constraints").data or {}
    saved = constraint_body(current, startDate=start, mustFinishBy=finish, **constraints)
    client.call("PUT", f"{base}/constraints", saved)
    return project
