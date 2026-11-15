"""Per-project dashboard overview: response models and how they are derived from facts.

Purpose: Nothing is invented: values that are not tracked stay null and the client labels them ("Not
tracked yet"). Risks, findings, milestone status and the next step all come from facts.
How it fits: `build_overview` turns the `OverviewFacts` that app/repositories/overview.py gathers
under RLS into the `ProjectOverview` app/api/v1/overview.py returns; layer: domain.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import ValidationError

from app.domain.common import CamelModel, ConstraintMode, ProjectStatus, ScopeStatus
from app.domain.notifications import Notification
from app.domain.optimization import RunKpi
from app.domain.overview_facts import (
    AppliedRunFacts,
    OverviewFacts,
    PolicyFacts,
    ProgressFacts,
    ScopeRisk,
)
from app.domain.overview_milestones import (
    MilestoneSummary,
    milestone_summary,
    planned_finishes,
)

PolicyKey = Literal["budget", "deadline", "capacity", "skills"]
StepKey = Literal["intake", "scope", "wbs", "constraints", "optimization", "changes"]
RiskLevel = Literal["High", "Medium"]
FindingSource = Literal["wbs_validate", "optimization"]

POLICY_NAMES: dict[PolicyKey, str] = {
    "budget": "Budget",
    "deadline": "Deadline",
    "capacity": "Employee capacity",
    "skills": "Required skills",
}
POLICY_LABELS: dict[ConstraintMode, str] = {
    ConstraintMode.HARD: "Must hold",
    ConstraintMode.SOFT: "Can flex",
}
MAX_FINDINGS_PER_SOURCE = 3


# --------------------------------------------------------------------------- response
class OverviewProject(CamelModel):
    """The project header of the overview."""

    id: uuid.UUID
    code: str
    name: str
    status: ProjectStatus
    due_date: date | None
    pm_name: str | None
    budget_cents: int | None
    currency: str
    progress_pct: int


class PlanViolations(CamelModel):
    """How many hard and soft constraints the applied plan misses."""

    hard: int
    soft: int


class AppliedPlan(CamelModel):
    """The plan in force: its version, KPIs, peak load and constraint misses."""

    version: int
    run_id: uuid.UUID
    applied_at: datetime | None
    expected_cost_cents: int | None
    completion_date: date | None
    kpis: list[RunKpi]
    peak_utilization: int | None
    violations: PlanViolations


class ConstraintChoice(CamelModel):
    """A constraint's policy as the PM chose it: must hold or can flex."""

    key: PolicyKey
    name: str
    mode: ConstraintMode
    label: str


class OverviewRisk(CamelModel):
    """A risk shown on the overview and where it comes from."""

    level: RiskLevel
    text: str
    source: str


class OverviewFinding(CamelModel):
    """An AI validation or optimizer finding shown on the overview."""

    text: str
    source: FindingSource


class NextStep(CamelModel):
    """The next workflow step to take and the screen it is on."""

    label: str
    step: StepKey


class ProjectOverview(CamelModel):
    """Everything the per-project dashboard shows."""

    project: OverviewProject
    applied_version: int | None
    plan: AppliedPlan | None
    constraint_choices: list[ConstraintChoice]
    milestones: MilestoneSummary
    risks: list[OverviewRisk]
    notifications: list[Notification]
    notification_total: int
    findings: list[OverviewFinding]
    # Spend is not tracked yet; the client shows "Not tracked yet".
    spent_to_date_cents: int | None
    next_step: NextStep


# --------------------------------------------------------------------------- derivations
def _utilization(result: dict[str, Any] | None) -> list[tuple[str, int]]:
    """Return (name, after-%) per person from a run result's utilization rows.

    Malformed rows are skipped, so an old or partial result never breaks the overview.
    """
    rows = (result or {}).get("utilization")
    out: list[tuple[str, int]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        after = row.get("after")
        if isinstance(after, int | float) and not isinstance(after, bool):
            out.append((str(row.get("name") or "Someone"), round(after)))
    return out


def peak_utilization(result: dict[str, Any] | None) -> int | None:
    """The plan's peak utilization (%), as its run reported it (`summary.peakUtilization`):
    the busiest day of anyone on the team, not an average. None when it reports none."""
    summary = (result or {}).get("summary")
    peak = summary.get("peakUtilization") if isinstance(summary, dict) else None
    percent = peak.get("percent") if isinstance(peak, dict) else None
    if isinstance(percent, int | float) and not isinstance(percent, bool):
        return round(percent)
    return None


def soft_violations(result: dict[str, Any] | None) -> int:
    """Soft-constraint misses, when the run result exposes them (else 0)."""
    data = result or {}
    violations = data.get("violations")
    if isinstance(violations, dict):
        soft = violations.get("soft")
        if isinstance(soft, int) and not isinstance(soft, bool):
            return max(soft, 0)
        if isinstance(soft, list):
            return len(soft)
    misses = data.get("softViolations")
    if isinstance(misses, int) and not isinstance(misses, bool):
        return max(misses, 0)
    if isinstance(misses, list):
        return len(misses)
    return 0


def run_kpis(result: dict[str, Any] | None) -> list[RunKpi]:
    """Return the run's KPI cards, skipping any that no longer match the KPI model."""
    rows = (result or {}).get("kpis")
    kpis: list[RunKpi] = []
    if not isinstance(rows, list):
        return kpis
    for row in rows:
        try:
            kpis.append(RunKpi.model_validate(row))
        except ValidationError:
            continue
    return kpis


def constraint_choices(policy: PolicyFacts | None) -> list[ConstraintChoice]:
    """Must hold / Can flex per constraint; the deadline follows the PM's answer."""
    policy_facts = policy or PolicyFacts()
    deadline = (
        ConstraintMode.SOFT if policy_facts.deadline_answer == "soft" else ConstraintMode.HARD
    )
    modes: dict[PolicyKey, ConstraintMode] = {
        "budget": policy_facts.budget_mode,
        "deadline": deadline,
        "capacity": policy_facts.capacity_mode,
        "skills": policy_facts.skill_mode,
    }
    return [
        ConstraintChoice(key=key, name=POLICY_NAMES[key], mode=mode, label=POLICY_LABELS[mode])
        for key, mode in modes.items()
    ]


def plan_risks(run: AppliedRunFacts | None) -> list[OverviewRisk]:
    """High risks the applied plan itself shows: anyone booked above 100%."""
    if run is None:
        return []
    return [
        OverviewRisk(
            level="High",
            text=f"{name} is booked at {after}% in Plan v{run.version}",
            source="From the plan",
        )
        for name, after in sorted(_utilization(run.result), key=lambda row: -row[1])
        if after > 100
    ]


def scope_risks(items: list[ScopeRisk]) -> list[OverviewRisk]:
    """Return accepted and still-pending risk scope items as medium risks."""
    return [
        OverviewRisk(
            level="Medium",
            text=item.text,
            source=(
                "From scope review"
                if item.status == ScopeStatus.ACCEPTED
                else "From AI intake, not reviewed yet"
            ),
        )
        for item in items
        if item.status in (ScopeStatus.ACCEPTED, ScopeStatus.PENDING)
    ]


def _plural(count: int, word: str) -> str:
    """Return `count word` with an English plural `s` when the count is not one."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _setup_todo(progress: ProgressFacts, policy: PolicyFacts | None) -> str | None:
    """What is still missing on Constraints: AI estimates to confirm, questions to answer."""
    unanswered = 0
    if policy is None or policy.specialist_answer is None:
        unanswered += 1
    if policy is None or policy.deadline_answer is None:
        unanswered += 1
    todo = []
    if progress.unconfirmed_ai_estimates:
        todo.append(f"confirm {_plural(progress.unconfirmed_ai_estimates, 'AI estimate')}")
    if unanswered:
        todo.append(f"answer {_plural(unanswered, 'question')}")
    return f"Set the constraints: {' and '.join(todo)}" if todo else None


def next_step(progress: ProgressFacts, policy: PolicyFacts | None, applied: bool) -> NextStep:
    """The first unfinished workflow step for this project."""
    if progress.scope_items == 0:
        return NextStep(label="Run AI Project Intake on your notes", step="intake")
    if progress.pending_scope_items > 0:
        return NextStep(
            label=f"Review {_plural(progress.pending_scope_items, 'scope item')} AI extracted",
            step="scope",
        )
    if progress.wbs_nodes == 0:
        return NextStep(label="Build the WBS from the accepted scope", step="wbs")
    todo = _setup_todo(progress, policy)
    if todo:
        return NextStep(label=todo, step="constraints")
    if applied:
        return NextStep(label="Record changes as they happen", step="changes")
    label = (
        "Review the optimized plan and apply it"
        if progress.has_successful_run
        else "Run the optimizer and apply a plan"
    )
    return NextStep(label=label, step="optimization")


def build_overview(facts: OverviewFacts, today: date) -> ProjectOverview:
    """Build the dashboard overview from the repository's facts.

    `today` decides which milestone is next; values that are not tracked stay null.
    """
    project, run = facts.project, facts.applied_run
    plan = None
    if run is not None:
        plan = AppliedPlan(
            version=run.version,
            run_id=run.run_id,
            applied_at=run.applied_at,
            expected_cost_cents=run.expected_cost_cents,
            completion_date=run.completion_date,
            kpis=run_kpis(run.result),
            peak_utilization=peak_utilization(run.result),
            # Only optimal/feasible runs can be applied, so every hard constraint holds.
            violations=PlanViolations(hard=0, soft=soft_violations(run.result)),
        )
    findings = [
        OverviewFinding(text=text, source="wbs_validate")
        for text in facts.validation_issues[:MAX_FINDINGS_PER_SOURCE]
    ] + [
        OverviewFinding(text=text, source="optimization")
        for text in facts.recommendation_reasons[:MAX_FINDINGS_PER_SOURCE]
    ]
    return ProjectOverview(
        project=OverviewProject(
            id=project.id,
            code=project.code,
            name=project.name,
            status=project.status,
            due_date=project.target_end_date,
            pm_name=project.pm_name,
            budget_cents=project.budget_cents,
            currency=project.currency,
            progress_pct=project.progress_pct,
        ),
        applied_version=run.version if run else None,
        plan=plan,
        constraint_choices=constraint_choices(facts.policy),
        milestones=milestone_summary(
            facts.milestones,
            run.completion_date if run else None,
            planned_finishes(run.result if run else None),
            today,
        ),
        risks=plan_risks(run) + scope_risks(facts.scope_risks),
        notifications=facts.notifications,
        notification_total=max(facts.notification_total, len(facts.notifications)),
        findings=findings,
        spent_to_date_cents=None,
        next_step=next_step(facts.progress, facts.policy, run is not None),
    )
