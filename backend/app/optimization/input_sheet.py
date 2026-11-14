"""Rows of "Check the inputs the solver will use" (Optimization Results, step 6).

Pure: `build_sheet(facts)` returns one row per input in the order the
screen shows them, each with its source, plus the blockers that keep the
run from starting. Sources: stated (the notes say it), confirmed (a person
confirmed it), needs_you (missing: blocks the run, or a warning such as a
Required skill no work package uses), default, assumed (the PM answered
"Don't know") and yours (entered by a person). Inferred values are never
used.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.optimization import formatting as fmt
from app.optimization.costing import reserve_cents
from app.optimization.inputs import InputFacts
from app.optimization.model import DEFAULT_HOURLY_RATE_CENTS
from app.optimization.remedies import DEFAULT_SPECIALIST_RATE_CENTS

Source = Literal["stated", "confirmed", "needs_you", "default", "assumed", "yours"]

POLICY = {"hard": "must hold", "soft": "can flex"}
DEPENDENCY_NAMES = {
    "FS": "finish-to-start",
    "SS": "start-to-start",
    "FF": "finish-to-finish",
    "SF": "start-to-finish",
}
NOT_IN_NOTES = "Not in notes"
QUESTION = "Question on step 5"
DONT_KNOW = "you said Don\u2019t know"


@dataclass(frozen=True, slots=True)
class Row:
    key: str
    label: str
    value: str
    source: Source
    from_: str


@dataclass(frozen=True, slots=True)
class Blocker:
    """Something a person must do before the solver may run."""

    key: str  # listed in the 422 problem's `missing`, e.g. "estimates"
    message: str  # "Confirm 3 AI estimates on step 4"


@dataclass(frozen=True, slots=True)
class Sheet:
    rows: list[Row]
    blockers: list[Blocker]

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def blocking(self) -> list[str]:
        return [blocker.message for blocker in self.blockers]

    @property
    def missing(self) -> list[str]:
        return [blocker.key for blocker in self.blockers]


Line = tuple[Row, Blocker | None]


def _names(names: Sequence[str], limit: int = 6) -> str:
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])} and {fmt.plural(len(names) - limit, 'other')}"


def _effort(days: int, per_week: int) -> str:
    if days < per_week:
        return fmt.plural(days, "day")
    return fmt.plural(round(days / per_week), "week")


def _policy(mode: str) -> str:
    return POLICY.get(mode, mode)


def _work_packages(facts: InputFacts) -> Line:
    label, where = "Work packages", "Step 4"
    if facts.work_packages == 0:
        row = Row("workPackages", label, "No work packages with durations yet", "needs_you", where)
        return row, Blocker("workPackages", "Add work packages with durations on step 4")
    count = fmt.plural(facts.work_packages, "work package")
    if facts.unconfirmed_estimates:
        estimates = fmt.plural(facts.unconfirmed_estimates, "AI estimate")
        row = Row("workPackages", label, f"{count} · {estimates} not confirmed", "needs_you", where)
        return row, Blocker("estimates", f"Confirm {estimates} on step 4")
    effort = _effort(facts.effort_days, facts.working_days_per_week)
    # Any AI estimate in the plan was confirmed by a person; otherwise all were entered by hand.
    source: Source = "confirmed" if facts.confirmed_ai_estimates else "yours"
    return Row("workPackages", label, f"{count} · {effort} of effort", source, where), None


def _dependencies(facts: InputFacts) -> Line:
    total = sum(facts.dependency_types.values())
    if not total:
        return Row("dependencies", "Dependencies", "None", "default", "Step 5"), None
    kinds = ", ".join(
        f"{count} {DEPENDENCY_NAMES.get(kind, kind)}"
        for kind, count in sorted(facts.dependency_types.items())
    )
    return Row("dependencies", "Dependencies", f"{total} ({kinds})", "confirmed", "Step 5"), None


def _budget(facts: InputFacts) -> Line:
    if facts.budget_cents is None:
        return Row("budget", "Budget", "No budget set", "default", NOT_IN_NOTES), None
    value = f"{fmt.money(facts.budget_cents)} · {_policy(facts.budget_mode)}"
    if facts.budget_quote:
        return Row("budget", "Budget", value, "stated", f"\u201c{facts.budget_quote}\u201d"), None
    where = "Step 5" if facts.budget_from_constraints else "Step 1"
    return Row("budget", "Budget", value, "yours", where), None


def _deadline(facts: InputFacts) -> Line:
    if facts.must_finish_by is None:
        return Row("deadline", "Deadline", "No deadline set", "default", NOT_IN_NOTES), None
    if facts.deadline_answer is None:
        row = Row("deadline", "Deadline", "Not answered", "needs_you", QUESTION)
        return row, Blocker("deadline", "Answer the deadline question on step 5")
    day = fmt.day(facts.must_finish_by)
    if facts.deadline_answer == "unknown":
        # "Don't know" is planned as a hard deadline: the safe assumption.
        return Row("deadline", "Deadline", f"{day} · hard ({DONT_KNOW})", "assumed", QUESTION), None
    mode = "can move" if facts.deadline_answer == "soft" else "hard"
    return Row("deadline", "Deadline", f"{day} · {mode}", "confirmed", QUESTION), None


def _specialists(facts: InputFacts) -> Line:
    label = "Outside specialists"
    if facts.specialist_answer == "yes":
        rate = facts.contractor_rate_cents or DEFAULT_SPECIALIST_RATE_CENTS
        estimated = "" if facts.specialist_rate_confirmed else " (estimated)"
        value = f"Allowed · ${rate / 100:,.0f}/h{estimated}"
        return Row("specialists", label, value, "confirmed", QUESTION), None
    if facts.specialist_answer == "no":
        return Row("specialists", label, "Not allowed", "confirmed", QUESTION), None
    if facts.specialist_answer == "unknown":
        # "Don't know" is planned as no specialists: nothing is spent without a yes.
        value = f"Not allowed ({DONT_KNOW})"
        return Row("specialists", label, value, "assumed", QUESTION), None
    if facts.skill_gaps:
        row = Row("specialists", label, "Not answered", "needs_you", QUESTION)
        return row, Blocker("specialist", "Answer the specialist question on step 5")
    value = "Not needed: the team covers every required skill"
    return Row("specialists", label, value, "default", "Step 5"), None


def _team(facts: InputFacts) -> Line:
    capacity = f"capacity {_policy(facts.capacity_mode)}"
    if not facts.team:
        return Row("team", "Team", "Nobody yet", "needs_you", "Step 5"), Blocker(
            "team", "Add your team on step 5"
        )
    if facts.team_from_project:
        value = f"{_names(facts.team)} · {capacity}"
        return Row("team", "Team", value, "confirmed", "Step 5"), None
    value = (
        "No project team yet, so everyone active in the organization: "
        f"{_names(facts.team)} · {capacity}"
    )
    return Row("team", "Team", value, "default", "Organization"), None


def _employee_cost(facts: InputFacts) -> Line:
    """Employees are costed at their company rate; without one, at the standard rate (D1)."""
    label, where = "Employee cost", "Company record"
    if not facts.employees:
        return Row("employeeCost", label, "No employees on the team", "default", where), None
    standard = len(facts.standard_rate_employees)
    own = len(facts.employees) - standard
    if not standard:
        value = f"{fmt.plural(own, 'employee')} at their company rate"
        return Row("employeeCost", label, value, "confirmed", where), None
    value = (
        f"{fmt.plural(standard, 'employee')} at the standard "
        f"${DEFAULT_HOURLY_RATE_CENTS / 100:,.0f}/h (no company rate on record)"
    )
    if own:
        value += f" · {own} at their company rate"
    return Row("employeeCost", label, value, "default", where), None


def _skills(facts: InputFacts) -> Line:
    if not facts.skills and not facts.skill_gaps:
        return Row("skills", "Required skills", "None listed", "default", "Step 5"), None
    required = sum(1 for skill in facts.skills if skill.required) or len(facts.skill_gaps)
    gaps = facts.skill_gaps
    cover = f"nobody has {', '.join(gaps)} yet" if gaps else "all covered"
    value = f"{required} · {cover} · {_policy(facts.skill_mode)}"
    return Row("skills", "Required skills", value, "confirmed", "Step 5"), None


def _unlinked_skills(facts: InputFacts) -> list[Row]:
    """A warning per Required skill no work package uses: the solver can't plan for it."""
    return [
        Row(
            f"skillUnlinked:{name}",
            name,
            f"{name} is required but no work package uses it. Tag the packages on step 4.",
            "needs_you",
            "Step 4",
        )
        for name in facts.unlinked_skills
    ]


def _contingency(facts: InputFacts) -> Line:
    """'Reserve · 10% = $110,000 held back': the plan may spend the budget less it."""
    if facts.contingency_pct <= 0:
        return Row("contingency", "Reserve", "0%", "default", NOT_IN_NOTES), None
    value = f"{facts.contingency_pct:g}%"
    if facts.budget_cents is not None:
        held = reserve_cents(facts.budget_cents, facts.contingency_pct)
        value += f" = {fmt.money(held)} held back"
    return Row("contingency", "Reserve", value, "yours", "Step 5"), None


def _working_days(facts: InputFacts) -> Line:
    value = f"{facts.working_days_per_week} per week"
    if facts.working_days_per_week != 5:
        return Row("workingDays", "Working days", value, "yours", "Step 5"), None
    return Row("workingDays", "Working days", value, "default", NOT_IN_NOTES), None


def _milestones(facts: InputFacts) -> Line:
    if not facts.milestones:
        return Row("milestones", "Phase milestones", "None", "default", NOT_IN_NOTES), None
    days = [fmt.short_day(milestone.due_date) for milestone in facts.milestones]
    modes = {milestone.mode for milestone in facts.milestones}
    mode = "hard and soft" if len(modes) > 1 else next(iter(modes))
    listed = days[0] if len(days) == 1 else f"{', '.join(days[:-1])} and {days[-1]}"
    return Row("milestones", "Phase milestones", f"{listed} · {mode}", "yours", "Step 5"), None


SECTIONS = (
    _work_packages,
    _dependencies,
    _budget,
    _deadline,
    _specialists,
    _team,
    _employee_cost,
    _skills,
    _contingency,
    _working_days,
    _milestones,
)


def build_sheet(facts: InputFacts) -> Sheet:
    """Every input row in screen order, and what still blocks the run."""
    rows: list[Row] = []
    blockers: list[Blocker] = []
    for section in SECTIONS:
        row, blocker = section(facts)
        rows.append(row)
        if blocker is not None:
            blockers.append(blocker)
        if section is _skills:
            rows += _unlinked_skills(facts)
    return Sheet(rows=rows, blockers=blockers)


def blocking_detail(blocking: Sequence[str]) -> str:
    """'Confirm 3 AI estimates on step 4 and add your team on step 5 before running …'."""
    parts = [
        message if position == 0 else message[0].lower() + message[1:]
        for position, message in enumerate(blocking)
    ]
    joined = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return f"{joined} before running the optimizer."
