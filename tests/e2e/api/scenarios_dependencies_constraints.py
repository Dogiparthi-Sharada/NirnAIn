"""API end-to-end scenarios for step 5: dependencies, limits (Must hold / Can flex) and questions.

Purpose: Exercise the dependency editor (add, self, duplicate, cycle, lag, edit, remove) and
the limits form with out-of-range values, then check that answering the step-5 questions
makes the optimizer inputs ready.
How it fits: `run_api_suite.py` runs areas 5c and 5d last before optimizing; the saved
limits are what the step-6 run respects; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from suite_client import ApiClient
from suite_fixtures import QaProject, constraint_body
from suite_report import ScenarioLog

DEPENDENCY_AREA = "5c. Dependencies"
CONSTRAINT_AREA = "5d. Constraints (Must hold / Can flex) and questions"
MINIMUM_PACKAGES = 3
INVALID_LIMITS = (
    ("Working days per week = 0", {"workingDaysPerWeek": 0}),
    ("Working days per week = 8", {"workingDaysPerWeek": 8}),
    ("Contingency 101%", {"contingencyPct": 101}),
    ("Negative budget", {"budgetTotalCents": -1}),
    ("Mode 'maybe'", {"budgetMode": "maybe"}),
    ("Specialist answer 'perhaps'", {"specialistAnswer": "perhaps"}),
)
PLANNED_LIMITS = {
    "budgetTotalCents": 45_000_000,
    "budgetMode": "hard",
    "startDate": "2026-11-30",
    "mustFinishBy": "2027-06-30",
    "deadlineMode": "hard",
    "capacityMode": "soft",
    "skillMode": "hard",
    "specialistAnswer": "yes",
    "deadlineAnswer": "hard",
    "specialistRateConfirmed": True,
    "contractorRateCents": 15_000,
    "allowContractors": True,
}


def unlinked_pairs(project: QaProject, existing: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Ordered package pairs not yet linked in either direction (the template adds links)."""
    linked = {(link["predecessorId"], link["successorId"]) for link in existing}
    linked |= {(successor, predecessor) for predecessor, successor in linked}
    return [
        (source, target)
        for source in project.package_ids
        for target in project.package_ids
        if source != target and (source, target) not in linked
    ]


def rejected_links(
    client: ApiClient, log: ScenarioLog, base: str, pair: tuple[str, str], third: str
) -> None:
    """Self, duplicate, cyclic and over-long-lag dependencies must all be refused."""
    first, second = pair
    for label, body, expected in (
        ("Self-dependency rejected", {"predecessorId": first, "successorId": first}, {409, 422}),
        (
            "Duplicate dependency rejected",
            {"predecessorId": first, "successorId": second},
            {409, 422},
        ),
        (
            "Cycle (B→A when A→B exists) rejected",
            {"predecessorId": second, "successorId": first},
            {409, 422},
        ),
    ):
        log.status(
            DEPENDENCY_AREA, label, client.call("POST", base, {**body, "type": "FS"}), expected
        )
    long_lag = {"predecessorId": first, "successorId": third, "type": "FS", "lagDays": 400}
    log.status(
        DEPENDENCY_AREA, "Lag over 365 days rejected", client.call("POST", base, long_lag), {422}
    )


def dependency_scenarios(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """Step 5 dependencies: add, self, duplicate, cycle, edit, remove."""
    if len(project.package_ids) < MINIMUM_PACKAGES:
        log.blocked(DEPENDENCY_AREA, "Dependencies", "editable", "fewer than 3 work packages")
        return
    base = f"/projects/{project.project_id}/dependencies"
    existing = client.call("GET", base).data or []
    unlinked = unlinked_pairs(project, existing)
    log.check(
        DEPENDENCY_AREA,
        "WBS template's own dependencies listed",
        "list",
        f"{len(existing)} existing links",
        True,
    )
    if not unlinked:
        reason = "every pair already linked"
        log.blocked(DEPENDENCY_AREA, "New dependencies", "an unlinked package pair", reason)
        return
    first, second = unlinked[0]
    third = next(
        (target for source, target in unlinked if source == first and target != second), None
    )
    if third is None:
        log.blocked(DEPENDENCY_AREA, "New dependencies", "a second unlinked successor", "none left")
        return
    link_body = {"predecessorId": first, "successorId": second, "type": "FS", "lagDays": 0}
    link = client.call("POST", base, link_body)
    log.status(DEPENDENCY_AREA, "Add finish-to-start dependency", link, {201})
    rejected_links(client, log, base, (first, second), third)
    if link.status == 201:
        changed = client.call(
            "PATCH", f"/dependencies/{link.data['id']}", {"type": "SS", "lagDays": 2}
        )
        log.status(DEPENDENCY_AREA, "Change type to start-to-start with 2-day lag", changed, {200})
    temporary = client.call(
        "POST", base, {"predecessorId": first, "successorId": third, "type": "FS"}
    )
    if temporary.status == 201:
        removed = client.call("DELETE", f"/dependencies/{temporary.data['id']}")
        log.status(DEPENDENCY_AREA, "Remove a dependency", removed, {204})


def constraint_scenarios(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """Step 5 limits and questions, then the optimizer's readiness check."""
    path = f"/projects/{project.project_id}/constraints"
    inputs_path = f"/projects/{project.project_id}/optimization/inputs"
    current = client.call("GET", path).data or {}
    before = client.call("GET", inputs_path).data or {}
    log.check(
        CONSTRAINT_AREA,
        "Before answering: Run is blocked with reasons",
        "ready=false with blocking reasons",
        f"ready={before.get('ready')} blocking={before.get('blocking')}",
        before.get("ready") is False,
    )
    for label, changes in INVALID_LIMITS:
        answer = client.call("PUT", path, constraint_body(current, **changes))
        log.status(CONSTRAINT_AREA, f"{label} rejected", answer, {422})
    backwards = client.call(
        "PUT", path, constraint_body(current, startDate="2027-06-30", mustFinishBy="2026-12-01")
    )
    log.status(CONSTRAINT_AREA, "Must-finish-by before start date rejected", backwards, {422})
    valid = constraint_body(current, **PLANNED_LIMITS)
    log.status(
        CONSTRAINT_AREA,
        "Save limits: budget $450K Must hold, deadline Jun 30 Must hold, capacity Can flex, "
        "skills Must hold, specialist yes at $150/h",
        client.call("PUT", path, valid),
        {200},
    )
    flipped_body = {
        **valid,
        "capacityMode": "hard",
        "deadlineMode": "soft",
        "deadlineAnswer": "soft",
    }
    flipped = client.call("PUT", path, flipped_body)
    log.status(
        CONSTRAINT_AREA, "Flip capacity to Must hold and deadline to Can flex", flipped, {200}
    )
    restored = client.call("PUT", path, valid)
    log.status(CONSTRAINT_AREA, "Restore the planned settings", restored, {200})
    after = client.call("GET", inputs_path).data or {}
    log.check(
        CONSTRAINT_AREA,
        "After answering: optimizer inputs ready",
        "ready=true, nothing blocking",
        f"ready={after.get('ready')} blocking={after.get('blocking')} "
        f"missing={after.get('missing')}",
        after.get("ready") is True,
    )
