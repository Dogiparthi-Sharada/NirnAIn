"""Per-project dashboard overview.

The repository gathers plain facts (`OverviewFacts`) under the caller's RLS session;
`build_overview` turns them into what the dashboard shows. Nothing is invented: values
that are not tracked stay null and the client labels them ("Not tracked yet").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import ValidationError

from app.domain.common import CamelModel, ConstraintMode, ProjectStatus, ScopeStatus
from app.domain.notifications import Notification
from app.domain.optimization import RunKpi
from app.domain.projects import Project

PolicyKey = Literal["budget", "deadline", "capacity", "skills"]
StepKey = Literal["intake", "scope", "wbs", "constraints", "optimization", "changes"]
MilestoneStatus = Literal["On track", "At risk", "Not planned yet"]
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


# ------------------------------------------------------------------- facts (from the repository)
@dataclass(frozen=True, slots=True)
class PolicyFacts:
    """The project's `constraint_sets` row; None fields mean "not answered yet"."""

    budget_mode: ConstraintMode = ConstraintMode.HARD
    capacity_mode: ConstraintMode = ConstraintMode.SOFT
    skill_mode: ConstraintMode = ConstraintMode.HARD
    deadline_answer: str | None = None
    specialist_answer: str | None = None


@dataclass(frozen=True, slots=True)
class MilestoneFact:
    name: str
    due_date: date


@dataclass(frozen=True, slots=True)
class AppliedRunFacts:
    run_id: uuid.UUID
    version: int
    applied_at: datetime | None
    expected_cost_cents: int | None
    completion_date: date | None
    result: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ScopeRisk:
    text: str
    status: ScopeStatus


@dataclass(frozen=True, slots=True)
class ProgressFacts:
    scope_items: int = 0
    pending_scope_items: int = 0
    wbs_nodes: int = 0
    unconfirmed_ai_estimates: int = 0
    has_successful_run: bool = False


@dataclass(slots=True)
class OverviewFacts:
    project: Project
    policy: PolicyFacts | None = None
    progress: ProgressFacts = field(default_factory=ProgressFacts)
    milestones: list[MilestoneFact] = field(default_factory=list)
    applied_run: AppliedRunFacts | None = None
    scope_risks: list[ScopeRisk] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)
    notification_total: int = 0
    validation_issues: list[str] = field(default_factory=list)
    recommendation_reasons: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- response
class OverviewProject(CamelModel):
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
    hard: int
    soft: int


class AppliedPlan(CamelModel):
    version: int
    run_id: uuid.UUID
    applied_at: datetime | None
    expected_cost_cents: int | None
    completion_date: date | None
    kpis: list[RunKpi]
    peak_utilization: int | None
    violations: PlanViolations


class ConstraintChoice(CamelModel):
    key: PolicyKey
    name: str
    mode: ConstraintMode
    label: str


class NextMilestone(CamelModel):
    name: str
    due_date: date
    status: MilestoneStatus


class MilestoneSummary(CamelModel):
    total: int
    next: NextMilestone | None


class OverviewRisk(CamelModel):
    level: RiskLevel
    text: str
    source: str


class OverviewFinding(CamelModel):
    text: str
    source: FindingSource


class NextStep(CamelModel):
    label: str
    step: StepKey


class ProjectOverview(CamelModel):
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
    p = policy or PolicyFacts()
    deadline = ConstraintMode.SOFT if p.deadline_answer == "soft" else ConstraintMode.HARD
    modes: dict[PolicyKey, ConstraintMode] = {
        "budget": p.budget_mode,
        "deadline": deadline,
        "capacity": p.capacity_mode,
        "skills": p.skill_mode,
    }
    return [
        ConstraintChoice(key=key, name=POLICY_NAMES[key], mode=mode, label=POLICY_LABELS[mode])
        for key, mode in modes.items()
    ]


def milestone_summary(
    milestones: list[MilestoneFact], completion: date | None, today: date
) -> MilestoneSummary:
    upcoming = sorted((m for m in milestones if m.due_date >= today), key=lambda m: m.due_date)
    if not upcoming:
        return MilestoneSummary(total=len(milestones), next=None)
    first = upcoming[0]
    status: MilestoneStatus
    if completion is None:
        status = "Not planned yet"
    else:
        status = "On track" if completion <= first.due_date else "At risk"
    return MilestoneSummary(
        total=len(milestones),
        next=NextMilestone(name=first.name, due_date=first.due_date, status=status),
    )


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
        for name, after in sorted(_utilization(run.result), key=lambda r: -r[1])
        if after > 100
    ]


def scope_risks(items: list[ScopeRisk]) -> list[OverviewRisk]:
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


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


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
    p, run = facts.project, facts.applied_run
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
            id=p.id,
            code=p.code,
            name=p.name,
            status=p.status,
            due_date=p.target_end_date,
            pm_name=p.pm_name,
            budget_cents=p.budget_cents,
            currency=p.currency,
            progress_pct=p.progress_pct,
        ),
        applied_version=run.version if run else None,
        plan=plan,
        constraint_choices=constraint_choices(facts.policy),
        milestones=milestone_summary(facts.milestones, run.completion_date if run else None, today),
        risks=plan_risks(run) + scope_risks(facts.scope_risks),
        notifications=facts.notifications,
        notification_total=max(facts.notification_total, len(facts.notifications)),
        findings=findings,
        spent_to_date_cents=None,
        next_step=next_step(facts.progress, facts.policy, run is not None),
    )
