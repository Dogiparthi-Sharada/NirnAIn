"""Steps 6 and 7 of the demo seed: optimize, apply Plan v1, record a change, approve Plan v2.

Purpose: Make the calls the Optimization Results and Changes & Versions screens make, in the
same order and with the same bodies (`src/screens/changes/ChangeImpact.tsx`: re-plan with the
change's overrides, change id and baseline; "Use this option" re-plans with a `remedy`;
"Accept {date}" / "Approve" approves the run as it is). Nothing is chosen at random: when a
change moves the date, the cheapest option that keeps it is taken, else the new date.
How it fits: `seed_flows.Seeder` calls `optimize_and_apply` for applied projects and
`record_and_approve` for each `ChangeSpec`; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from seed_client import ApiClient, SeedError, wait_until
from seed_model import ChangeSpec

RUNNING = {"queued", "running"}
SOLVED = {"optimal", "feasible"}
RULES_MODEL = "rules"
SOLVER_TIMEOUT_S = 420.0


def money(cents: int | None) -> str:
    """$12,345 (or '—' when unknown)."""
    return "—" if cents is None else f"${cents / 100:,.0f}"


def summary_of(run: dict[str, Any]) -> dict[str, Any]:
    """`result.summary` of a run ({} when missing)."""
    return (run.get("result") or {}).get("summary") or {}


def describe(run: dict[str, Any]) -> str:
    """One line for the log: status, finish, cost, slip."""
    summary = summary_of(run)
    slip = summary.get("slipDays") or 0
    return (
        f"{run.get('status')}, finishes {summary.get('completionDate')}, "
        f"cost {money(summary.get('costCents'))}"
        + (f", {slip} working days after {summary.get('targetDate')}" if slip else "")
    )


def run_optimizer(
    api: ApiClient, project_id: str, body: dict[str, Any], step: str
) -> dict[str, Any]:
    """Starts a run and waits for the solver; returns the finished run (any status)."""
    started = api.expect(
        "POST", f"/projects/{project_id}/optimization/runs", body, step=step, ok=(201, 202)
    )
    path = f"/projects/{project_id}/optimization/runs/{started['id']}"
    finished: dict[str, Any] = wait_until(
        lambda: api.expect("GET", path, step=step),
        lambda run: run.get("status") not in RUNNING,
        step=step,
        timeout_s=SOLVER_TIMEOUT_S,
        interval_s=3.0,
    )
    if finished.get("status") == "failed":
        raise SeedError(f"{step}: the optimizer failed: {finished.get('error')}")
    return finished


def misses_date(run: dict[str, Any]) -> bool:
    """No plan, or a plan that finishes after the date it tries to keep."""
    return run.get("status") == "infeasible" or (summary_of(run).get("slipDays") or 0) > 0


def pick_remedy(run: dict[str, Any], target: str | None) -> dict[str, Any] | None:
    """The cheapest priced option that finishes by `target`; with no plan at all, the new date."""
    remedies: list[dict[str, Any]] = (run.get("result") or {}).get("remedies") or []
    keeping = [
        remedy
        for remedy in remedies
        if remedy.get("id") != "move_deadline"
        and (target is None or (remedy.get("completionDate") or "9999-12-31") <= target)
    ]
    if keeping:
        return min(keeping, key=lambda remedy: remedy.get("costCents") or 0)
    if run.get("status") == "infeasible":
        return next((remedy for remedy in remedies if remedy.get("id") == "move_deadline"), None)
    return None


def solve_or_explain(run: dict[str, Any], step: str) -> None:
    """Raises with the solver's reasons when a run has no plan."""
    if run.get("status") not in SOLVED:
        reasons = "; ".join(
            str(block.get("explanation") or block.get("name") or block)
            for block in run.get("iis") or []
        )
        raise SeedError(
            f"{step}: no plan ({run.get('status')}). {reasons or run.get('error') or ''}".strip()
        )


def settle_date(
    api: ApiClient,
    project_id: str,
    run: dict[str, Any],
    body: dict[str, Any],
    target: str | None,
    step: str,
    say: Callable[[str], None],
) -> tuple[dict[str, Any], str]:
    """When a plan misses its date, what the PM picks on the "Your call" card: the cheapest option
    that keeps the date ("Use this option"), else the new date ("Accept").

    Returns (run, decision).
    """
    if not misses_date(run):
        return run, "date holds"
    target = summary_of(run).get("targetDate") or target
    remedy = pick_remedy(run, target)
    if remedy is None:
        solve_or_explain(run, step)
        return run, f"accepted the new date {summary_of(run).get('completionDate')}"
    chosen = run_optimizer(
        api, project_id, {**body, "remedy": remedy["id"]}, f"{step} option '{remedy['id']}'"
    )
    solve_or_explain(chosen, f"{step} option '{remedy['id']}'")
    say(f"    option '{remedy['title']}': {describe(chosen)}")
    return chosen, f"chose '{remedy['title']}'"


def optimize_and_apply(api: ApiClient, project_id: str, say: Callable[[str], None]) -> None:
    """Step 6: checks the inputs sheet, runs the optimizer and applies the plan (Plan v1)."""
    inputs = api.expect("GET", f"/projects/{project_id}/optimization/inputs", step="Step 6 inputs")
    if not inputs.get("ready"):
        raise SeedError(
            f"Step 6: inputs not ready: blocking={inputs.get('blocking')} "
            f"missing={inputs.get('missing')}"
        )
    run = run_optimizer(api, project_id, {}, "Step 6 optimize")
    say(f"    optimized: {describe(run)}")
    run, _ = settle_date(api, project_id, run, {}, None, "Step 6", say)
    applied = api.expect(
        "POST",
        f"/projects/{project_id}/optimization/runs/{run['id']}/apply",
        {},
        step="Step 6 apply",
    )
    say(f"    applied as Plan v{applied.get('version')}")


def _record(
    api: ApiClient, project_id: str, change: ChangeSpec, ai_calls: str, team_ids: dict[str, str]
) -> dict[str, Any]:
    """Records the change from notes (rules) or, when AI calls are skipped, as a direct edit."""
    step = f"Step 7 '{change.label}'"
    if ai_calls == RULES_MODEL:
        body: dict[str, Any] = {"source": "notes", "text": change.notes}
    else:
        name, hours = change.edit_hours
        body = {"source": "edits", "edits": {"hoursPerWeek": {team_ids[name]: hours}}}
    recorded = api.expect("POST", f"/projects/{project_id}/changes", body, step=step, ok=(201, 202))
    path = f"/changes/{recorded['id']}"
    found: dict[str, Any] = wait_until(
        lambda: api.expect("GET", path, step=step),
        lambda item: item.get("status") not in {"draft", "analyzing"},
        step=step,
    )
    impact = found.get("impact") or {}
    if ai_calls == RULES_MODEL and impact.get("model") != RULES_MODEL:
        raise SeedError(
            f"{step}: the API read the notes with '{impact.get('model')}', not the rules. "
            "Stop: this API has an AI key, so seeding would spend Claude tokens."
        )
    detected = impact.get("changes") or []
    if not any(
        item.get("kind") == change.expect_kind
        and change.expect_target in str(item.get("targetLabel") or "")
        for item in detected
    ):
        raise SeedError(
            f"{step}: expected a {change.expect_kind} change for {change.expect_target}, found "
            f"{[(item.get('kind'), item.get('targetLabel'), item.get('to')) for item in detected]}"
        )
    questions = [question for question in impact.get("questions") or [] if question.get("options")]
    if questions:
        answers = {question["id"]: question["options"][0]["value"] for question in questions}
        found = api.expect("PATCH", path, {"answers": answers}, step=f"{step} answers")
    if not found.get("overrides"):
        raise SeedError(f"{step}: the change has nothing to re-plan (status {found.get('status')})")
    return found


def record_and_approve(
    api: ApiClient,
    project_id: str,
    change: ChangeSpec,
    ai_calls: str,
    team_ids: dict[str, str],
    say: Callable[[str], None],
) -> str:
    """Step 7: record → answer → re-plan (keeping the date if an option can) → compare → approve.
    Returns what was decided, e.g.
    "chose 'Overtime: …': finishes … (was …), $178,012 (was $174,000)".
    """
    step = f"Step 7 '{change.label}'"
    found = _record(api, project_id, change, ai_calls, team_ids)
    base = summary_of(
        api.expect(
            "GET", f"/projects/{project_id}/optimization/runs/{found['baseRunId']}", step=step
        )
    )
    replan = {
        "overrides": found["overrides"],
        "changeRequestId": found["id"],
        "baselineRunId": found["baseRunId"],
    }
    run = run_optimizer(api, project_id, replan, f"{step} re-plan")
    say(f"    re-planned: {describe(run)}")
    run, decision = settle_date(api, project_id, run, replan, base.get("completionDate"), step, say)
    solve_or_explain(run, f"{step} re-plan")
    api.expect(
        "GET", f"/changes/{found['id']}/compare", query={"runId": run["id"]}, step=f"{step} compare"
    )
    api.expect(
        "POST", f"/changes/{found['id']}/approve", {"runId": run["id"]}, step=f"{step} approve"
    )
    after = summary_of(run)
    return (
        f"{decision}: finishes {after.get('completionDate')} (was {base.get('completionDate')}), "
        f"{money(after.get('costCents'))} (was {money(base.get('costCents'))})"
    )
