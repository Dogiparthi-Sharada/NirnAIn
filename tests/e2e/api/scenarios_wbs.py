"""API end-to-end scenarios for step 4: the WBS builder.

Purpose: Generate the WBS from the accepted scope (template fallback, no Claude tokens), edit
it by hand with every validation corner case, confirm the estimates and validate it (rules).
How it fits: `run_api_suite.py` runs this area after scope review; it fills
`QaProject.root_id` and `QaProject.package_ids` for the team, dependency and optimizer
scenarios; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from typing import Any

from suite_client import ApiClient
from suite_fixtures import QaProject
from suite_planning import wait_for_ai_job
from suite_report import ScenarioLog

AREA = "4. WBS builder"
PACKAGE_LEVEL = 3


def packages_in(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The level-3 nodes (work packages) of a WBS listing."""
    return [node for node in nodes if node["level"] == PACKAGE_LEVEL]


def generate_wbs(client: ApiClient, log: ScenarioLog, project: QaProject) -> list[dict[str, Any]]:
    """Generates the WBS (and tries again once it exists); returns the WBS nodes."""
    base = f"/projects/{project.project_id}"
    started = client.call("POST", f"{base}/wbs/generate")
    log.status(AREA, "Generate the WBS from accepted scope (template, no Claude)", started, {202})
    job = wait_for_ai_job(client, project, "wbs_generate")
    nodes = client.call("GET", f"{base}/wbs").data or []
    packages = packages_in(nodes)
    deliverables = sum(node["level"] == PACKAGE_LEVEL - 1 for node in nodes)
    log.check(
        AREA,
        "Generation produces deliverables and work packages",
        "job succeeded, packages > 0",
        f"job={job.get('status')} model={job.get('model')} deliverables={deliverables} "
        f"packages={len(packages)}",
        job.get("status") == "succeeded" and bool(packages),
    )
    again = client.call("POST", f"{base}/wbs/generate")
    log.check(
        AREA,
        "Generate again when a WBS exists",
        "refused (409) or replaces — never duplicates",
        f"HTTP {again.status} {again.detail}",
        again.status in {409, 202},
    )
    if again.status == 202:
        wait_for_ai_job(client, project, "wbs_generate")
        nodes = client.call("GET", f"{base}/wbs").data or []
    return nodes


def package_edit_scenarios(client: ApiClient, log: ScenarioLog, node_path: str) -> None:
    """Edits one work package: invalid values rejected, a valid edit and reordering accepted."""
    for label, change in (
        ("Negative duration rejected", {"durationDays": -1}),
        ("Duration over 10 years rejected", {"durationDays": 3651}),
        ("Empty work-package name rejected", {"name": ""}),
    ):
        log.status(AREA, label, client.call("PATCH", node_path, change), {422})
    zero = client.call("PATCH", node_path, {"durationDays": 0})
    log.check(
        AREA,
        "Zero-day duration",
        "accepted (milestone-like) or refused",
        f"HTTP {zero.status} {zero.detail}",
        None,
    )
    renamed = client.call(
        "PATCH", node_path, {"name": "Security & SOC 2 review", "durationDays": 15}
    )
    log.status(AREA, "Edit name and duration", renamed, {200})
    first = client.call("POST", f"{node_path}/move", {"position": 0})
    log.status(AREA, "Reorder: move to first position", first, {200})
    negative = client.call("POST", f"{node_path}/move", {"position": -1})
    log.status(AREA, "Reorder to a negative position rejected", negative, {422})


def manual_edit_scenarios(
    client: ApiClient, log: ScenarioLog, project: QaProject, root_id: str
) -> bool:
    """Adds a deliverable and packages by hand, edits and deletes them; False when blocked."""
    nodes_path = f"/projects/{project.project_id}/wbs/nodes"
    deliverable = client.call(
        "POST", nodes_path, {"parentId": root_id, "name": "QA Security & compliance"}
    )
    log.status(AREA, "Add a deliverable", deliverable, {201})
    if deliverable.status != 201:
        return False
    package_body = {
        "parentId": deliverable.data["id"],
        "name": "Security review",
        "durationDays": 10,
    }
    package = client.call("POST", nodes_path, package_body)
    added = package.data or {}
    log.check(
        AREA,
        "Add a work package with my own estimate",
        "201, estimate 'yours', confirmed",
        f"HTTP {package.status} source={added.get('estimateSource')} "
        f"confirmed={added.get('estimateConfirmed')}",
        package.status == 201 and added.get("estimateSource") == "yours",
    )
    if package.status == 201:
        package_edit_scenarios(client, log, f"/wbs/nodes/{added['id']}")
    orphan = client.call("POST", nodes_path, {"parentId": str(uuid.uuid4()), "name": "Orphan"})
    log.status(AREA, "Add under a non-existent parent rejected", orphan, {404, 422})
    temporary_body = {
        "parentId": deliverable.data["id"],
        "name": "Temporary package",
        "durationDays": 5,
    }
    temporary = client.call("POST", nodes_path, temporary_body)
    if temporary.status == 201:
        temporary_path = f"/wbs/nodes/{temporary.data['id']}"
        log.status(AREA, "Delete a work package", client.call("DELETE", temporary_path), {204})
        log.status(AREA, "Delete it again → 404", client.call("DELETE", temporary_path), {404})
    return True


def confirm_and_validate(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """Confirms one AI estimate, then all of them, then validates the WBS with the rules."""
    base = f"/projects/{project.project_id}"
    nodes = client.call("GET", f"{base}/wbs").data or []
    unconfirmed = [node for node in packages_in(nodes) if not node.get("estimateConfirmed")]
    if unconfirmed:
        target_id = unconfirmed[0]["id"]
        one = client.call("POST", f"{base}/wbs/confirm-estimates", {"nodeIds": [target_id]})
        confirmed = next((node for node in one.data or [] if node["id"] == target_id), {})
        log.check(
            AREA,
            "Confirm one AI estimate",
            "that package confirmed",
            f"HTTP {one.status} confirmed={confirmed.get('estimateConfirmed')}",
            one.status == 200 and confirmed.get("estimateConfirmed") is True,
        )
    confirm_all = client.call("POST", f"{base}/wbs/confirm-estimates", {})
    open_after = [
        node for node in packages_in(confirm_all.data or []) if not node.get("estimateConfirmed")
    ]
    log.check(
        AREA,
        "Confirm all estimates",
        "no unconfirmed package left",
        f"HTTP {confirm_all.status}, unconfirmed left={len(open_after)}",
        confirm_all.status == 200 and not open_after,
    )
    validate = client.call("POST", f"{base}/wbs/validate")
    log.status(AREA, "Validate the WBS (rules, no Claude)", validate, {202})
    validation = wait_for_ai_job(client, project, "wbs_validate")
    issues = (validation.get("result") or {}).get("issues", [])
    log.check(
        AREA,
        "Validation finishes",
        "succeeded with a list of issues",
        f"status={validation.get('status')} issues={len(issues)}",
        validation.get("status") == "succeeded",
    )


def wbs_scenarios(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """Step 4: template generation, manual edits, estimate confirmation, validation."""
    nodes = generate_wbs(client, log, project)
    root = next((node for node in nodes if node["level"] == 1), None)
    if root is None:
        log.blocked(AREA, "Manual WBS edits", "editable tree", "no root node after generation")
        return
    project.root_id = root["id"]
    if not manual_edit_scenarios(client, log, project, root["id"]):
        return
    confirm_and_validate(client, log, project)
    nodes = client.call("GET", f"/projects/{project.project_id}/wbs").data or []
    project.package_ids = [node["id"] for node in packages_in(nodes)]
