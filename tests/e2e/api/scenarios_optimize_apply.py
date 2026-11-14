"""API end-to-end scenarios for step 6: optimize, read the result, export and apply Plan v1.

Purpose: Run the optimizer on the prepared project, check the plan is complete, priced and
within the Can-flex cap, export it (PDF, MS Project), apply it as Plan v1 and check the
versions, the dashboard overview and the notification.
How it fits: `run_api_suite.py` runs area 6 after step 5; the applied run id it returns is
the baseline for the round-2 and mid-project scenarios; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from typing import Any

from suite_client import ApiClient
from suite_fixtures import QaProject
from suite_planning import SOLVED, describe_run, remedies_of, run_optimizer, summary_of
from suite_report import ScenarioLog

AREA = "6. Optimization results and Plan v1"
FLEX_CAP_PERCENT = 125
EXPORTS = (("pdf", b"%PDF"), ("msproject", b"<?xml"))


def solved_run(client: ApiClient, log: ScenarioLog, project: QaProject) -> dict[str, Any] | None:
    """Runs the optimizer (trying the remedies when infeasible); returns a solved run or None."""
    start_status, run = run_optimizer(client, project)
    infeasible = run.get("status") == "infeasible"
    log.check(
        AREA,
        "Run the optimizer",
        "202 then optimal/feasible (or infeasible with priced remedies)",
        f"HTTP {start_status}; {describe_run(run)}",
        run.get("status") in SOLVED or (infeasible and bool(remedies_of(run))),
    )
    if infeasible:
        for remedy in ("specialist", "move_deadline"):
            _, remedied = run_optimizer(client, project, {"remedy": remedy})
            solved = remedied.get("status") in SOLVED
            log.check(
                AREA, f"Re-run with remedy '{remedy}'", "solves", describe_run(remedied), solved
            )
            if solved:
                run = remedied
                break
    if run.get("status") not in SOLVED:
        log.blocked(AREA, "Apply Plan v1, exports, lock, changes", "a solved plan", "no solved run")
        return None
    return run


def result_checks(log: ScenarioLog, project: QaProject, run: dict[str, Any]) -> None:
    """The plan schedules every package, has a real cost and respects the Can-flex cap."""
    schedule = (run.get("result") or {}).get("schedule") or []
    log.check(
        AREA,
        "Result has a schedule for every work package",
        f"{len(project.package_ids)} packages scheduled",
        f"{len(schedule)} schedule rows",
        len(schedule) >= len(project.package_ids) > 0,
    )
    plan_cost = summary_of(run).get("costCents") or 0
    log.check(
        AREA,
        "Plan cost is real (people added in the app are priced)",
        "cost > $0 (rate or the default rate)",
        f"cost=${plan_cost / 100:,.0f}",
        plan_cost > 0,
    )
    peak = summary_of(run).get("peakUtilization") or {}
    log.check(
        AREA,
        "Nobody is overbooked beyond the 25% Can-flex cap",
        "peak utilization ≤ 125%",
        f"peak={peak}",
        (peak.get("percent") or 0) <= FLEX_CAP_PERCENT,
    )


def export_checks(client: ApiClient, log: ScenarioLog, base: str, run_id: str) -> None:
    """PDF and MS Project exports are real files; an unknown format is refused."""
    export_path = f"{base}/optimization/runs/{run_id}/export"
    for export_format, magic in EXPORTS:
        exported = client.call("GET", export_path, query={"format": export_format})
        log.check(
            AREA,
            f"Export {export_format}",
            f"200, file starts with {magic.decode()}",
            f"HTTP {exported.status} {exported.headers.get('content-type')} "
            f"{len(exported.raw)} bytes",
            exported.status == 200 and exported.raw.startswith(magic),
        )
    unknown = client.call("GET", export_path, query={"format": "xlsx"})
    log.status(AREA, "Export unknown format rejected", unknown, {422})


def notification_checks(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """The 'Plan v1 applied' notification is there and can be marked read."""
    listed = client.call("GET", "/notifications").data or []
    notes = listed if isinstance(listed, list) else listed.get("items", [])
    applied_note = next(
        (
            note
            for note in notes
            if "Plan v1 applied" in str(note.get("message", ""))
            and project.name in str(note.get("message", ""))
        ),
        None,
    )
    log.check(
        AREA,
        "Notification 'Plan v1 applied'",
        "present",
        str(applied_note.get("message") if applied_note else None),
        applied_note is not None,
    )
    if applied_note:
        read = client.call("POST", f"/notifications/{applied_note['id']}/read")
        log.status(AREA, "Mark the notification read", read, {200, 204})


def optimize_and_apply(client: ApiClient, log: ScenarioLog, project: QaProject) -> str | None:
    """Step 6: run, read, export and apply Plan v1. Returns the applied run id."""
    base = f"/projects/{project.project_id}"
    run = solved_run(client, log, project)
    if run is None:
        return None
    result_checks(log, project, run)
    export_checks(client, log, base, run["id"])
    apply_path = f"{base}/optimization/runs/{run['id']}/apply"
    applied = client.call("POST", apply_path, {})
    version = (applied.data or {}).get("version")
    log.check(
        AREA,
        "Apply the plan",
        "200, version 1",
        f"HTTP {applied.status} version={version} {applied.detail}",
        applied.status == 200 and version == 1,
    )
    again = client.call("POST", apply_path, {})
    log.status(AREA, "Apply the same run again is refused", again, {409, 422})
    versions = [
        item.get("version") for item in client.call("GET", f"{base}/plan-versions").data or []
    ]
    log.check(AREA, "Plan versions list", "[v1]", str(versions), versions == [1])
    overview = client.call("GET", f"{base}/overview").data or {}
    log.check(
        AREA,
        "Dashboard overview shows the applied version",
        "appliedVersion = 1",
        f"appliedVersion={overview.get('appliedVersion')}",
        overview.get("appliedVersion") == 1,
    )
    notification_checks(client, log, project)
    return str(run["id"]) if applied.status == 200 else None
