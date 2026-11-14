"""Optimizer and step-7 change helpers shared by the API end-to-end scenarios.

Purpose: Run the optimizer and wait for it, describe a run in one line, and drive one
step-7 change through record → answer → re-plan → compare → approve or reject, logging each
step, so the scenario modules read as a list of business cases.
How it fits: used by the optimize/apply, mid-project, round-2 and corner-case scenario
modules; built on `suite_client` and `suite_report`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from suite_client import ApiClient, wait_until
from suite_fixtures import QaProject
from suite_report import ScenarioLog

RUNNING = {"queued", "running"}
SOLVED = {"optimal", "feasible"}
TERMINAL_JOB = {"succeeded", "failed", "cancelled"}
ANALYSING = {"draft", "analyzing"}
SOLVER_TIMEOUT_S = 240.0


@dataclass(frozen=True)
class ChangeCase:
    """One step-7 change: the report area, its label, the request body and the PM's decision."""

    area: str
    label: str
    body: dict[str, Any]
    decision: str  # "approve" or "reject"


@dataclass
class ChangeOutcome:
    """What a driven change left behind: the change, the approved run id and the re-plan run."""

    change: dict[str, Any] = field(default_factory=dict)
    approved_run: str | None = None
    replan: dict[str, Any] = field(default_factory=dict)


def summary_of(run: dict[str, Any]) -> dict[str, Any]:
    """The run's `result.summary` block ({} when missing)."""
    return (run.get("result") or {}).get("summary") or {}


def remedies_of(run: dict[str, Any]) -> list[dict[str, Any]]:
    """The run's priced remedies ([] when none)."""
    return (run.get("result") or {}).get("remedies") or []


def run_optimizer(
    client: ApiClient, project: QaProject, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    """Starts a run and waits for the solver; returns (start status, final run)."""
    runs_path = f"/projects/{project.project_id}/optimization/runs"
    started = client.call("POST", runs_path, body if body is not None else {})
    if started.status not in {201, 202}:
        return started.status, {"error": started.detail}
    final = wait_until(
        lambda: client.call("GET", f"{runs_path}/{started.data['id']}"),
        lambda answer: (answer.data or {}).get("status") not in RUNNING,
        timeout_s=SOLVER_TIMEOUT_S,
    )
    return started.status, final.data or {}


def describe_run(run: dict[str, Any]) -> str:
    """One line: status, finish date, cost, peak utilization, remedies."""
    summary = summary_of(run)
    remedies = [
        f"{remedy['id']} ${(remedy.get('costCents') or 0) / 100:,.0f}"
        for remedy in remedies_of(run)
    ]
    cost = summary.get("costCents")
    cost_text = "$" + format(cost / 100, ",.0f") if cost is not None else None
    return (
        f"status={run.get('status')} finish={summary.get('completionDate')} "
        f"daysLate={summary.get('daysLate')} cost={cost_text} "
        f"peak={summary.get('peakUtilization')} remedies={remedies or 'none'} "
        f"error={run.get('error')}"
    )


def versions_of(client: ApiClient, project: QaProject) -> list[int]:
    """The applied plan version numbers, oldest first."""
    listed = client.call("GET", f"/projects/{project.project_id}/plan-versions").data or []
    return [version.get("version") for version in listed]


def wait_for_ai_job(client: ApiClient, project: QaProject, kind: str) -> dict[str, Any]:
    """Polls the latest AI job of one kind until it finishes."""
    latest_path = f"/projects/{project.project_id}/ai-jobs/latest"
    answer = wait_until(
        lambda: client.call("GET", latest_path, query={"kind": kind}),
        lambda latest: (latest.data or {}).get("status") in TERMINAL_JOB,
    )
    return answer.data or {}


def analyse_change(
    client: ApiClient, log: ScenarioLog, case: ChangeCase, change_id: str
) -> dict[str, Any]:
    """Waits for the change analysis, logs what was detected and answers every question."""
    change_path = f"/changes/{change_id}"
    change = (
        wait_until(
            lambda: client.call("GET", change_path),
            lambda answer: (answer.data or {}).get("status") not in ANALYSING,
        ).data
        or {}
    )
    impact = change.get("impact") or {}
    detected = [
        f"{item.get('kind')}:{item.get('targetLabel') or item.get('target')} "
        f"{item.get('from')}→{item.get('to')}"
        for item in impact.get("changes") or []
    ]
    questions = impact.get("questions") or []
    log.check(
        case.area,
        f"{case.label}: change analysed",
        "status ready with detected changes",
        f"status={change.get('status')} model={impact.get('model')} changes={detected} "
        f"questions={len(questions)}",
        change.get("status") == "ready" and bool(detected),
    )
    if not questions:
        return change
    answers = {
        question["id"]: question["options"][0]["value"]
        for question in questions
        if question.get("options")
    }
    answered = client.call("PATCH", change_path, {"answers": answers})
    overrides = (answered.data or {}).get("overrides")
    log.check(
        case.area,
        f"{case.label}: answer {len(answers)} question(s)",
        "200 with re-plan overrides",
        f"HTTP {answered.status} overrides={overrides}",
        answered.status == 200 and bool(overrides),
    )
    return answered.data or change


def decide_change(
    client: ApiClient,
    log: ScenarioLog,
    case: ChangeCase,
    change: dict[str, Any],
    run: dict[str, Any],
) -> ChangeOutcome:
    """Approves the re-plan (when asked and solved) or rejects the change, keeping the plan."""
    change_path = f"/changes/{change['id']}"
    if case.decision == "approve" and run.get("status") in SOLVED:
        approved = client.call("POST", f"{change_path}/approve", {"runId": run["id"]})
        log.check(
            case.area,
            f"{case.label}: approve",
            "200, status approved",
            f"HTTP {approved.status} status={(approved.data or {}).get('status')} "
            f"{approved.detail}",
            approved.status == 200,
        )
        approved_run = str(run["id"]) if approved.status == 200 else None
        return ChangeOutcome(approved.data or change, approved_run, run)
    rejected = client.call("POST", f"{change_path}/reject", {})
    log.check(
        case.area,
        f"{case.label}: reject (keep current plan)",
        "200, status rejected",
        f"HTTP {rejected.status} status={(rejected.data or {}).get('status')}",
        rejected.status == 200,
    )
    return ChangeOutcome(rejected.data or change, None, run)


def drive_change(
    client: ApiClient, log: ScenarioLog, project: QaProject, case: ChangeCase, baseline_run: str
) -> ChangeOutcome:
    """Records a change, answers its questions, re-plans, compares, then approves or rejects."""
    recorded = client.call("POST", f"/projects/{project.project_id}/changes", case.body)
    if not log.status(case.area, f"{case.label}: record the change", recorded, {201, 202}):
        return ChangeOutcome(recorded.data or {})
    change = analyse_change(client, log, case, recorded.data["id"])
    overrides = change.get("overrides")
    if not overrides:
        reason = f"no overrides (status={change.get('status')})"
        log.blocked(case.area, f"{case.label}: re-plan", "overrides available", reason)
        return ChangeOutcome(change)
    replan_body = {
        "overrides": overrides,
        "changeRequestId": change["id"],
        "baselineRunId": baseline_run,
    }
    start_status, run = run_optimizer(client, project, replan_body)
    solved = run.get("status") in SOLVED
    log.check(
        case.area,
        f"{case.label}: re-plan",
        "solved, or infeasible WITH priced remedies",
        f"HTTP {start_status}; {describe_run(run)}",
        solved or (run.get("status") == "infeasible" and bool(remedies_of(run))),
    )
    comparison = client.call(
        "GET", f"/changes/{change['id']}/compare", query={"runId": run.get("id", "")}
    )
    log.check(
        case.area,
        f"{case.label}: compare new plan vs current",
        "200 with rows",
        f"HTTP {comparison.status} rows={len((comparison.data or {}).get('rows') or [])} "
        f"{comparison.detail}",
        comparison.status == 200 or not solved,
    )
    return decide_change(client, log, case, change, run)
