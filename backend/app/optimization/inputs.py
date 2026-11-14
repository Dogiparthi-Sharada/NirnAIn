"""Facts behind the optimizer's confirmation sheet, and the inputs fingerprint.

`facts_from_input` reads what the frozen planning input already holds; the
repository adds what only the database knows (estimate confirmations, the
answers to the step-5 questions, the project's skill list, where the budget
came from). `app/optimization/input_sheet.py` turns the facts into rows.
`inputs_hash` fingerprints exactly what the solver will use, so the screen
can show "Inputs locked · hash 3f9a1c…".
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from app.optimization.model import PlanningInput
from app.optimization.remedies import covers

# "$375K", "$375,000", "$1.2M", "1.2 million": a number with an optional unit.
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)(k|m)?")
_UNIT = {"k": 1_000, "m": 1_000_000}


@dataclass(frozen=True, slots=True)
class SkillFact:
    """A skill on the project's list and whether anyone on the team has it."""

    name: str
    required: bool
    covered: bool


@dataclass(frozen=True, slots=True)
class MilestoneFact:
    """A phase milestone the solver checks, with its policy."""

    name: str
    due_date: date
    mode: str


@dataclass(frozen=True, slots=True)
class InputFacts:
    """Everything the sheet shows, already resolved to the values the solver uses."""

    work_packages: int = 0
    effort_days: int = 0
    confirmed_ai_estimates: int = 0
    unconfirmed_estimates: int = 0
    dependency_types: Mapping[str, int] = field(default_factory=dict)
    budget_cents: int | None = None
    budget_mode: str = "hard"
    budget_quote: str | None = None
    budget_from_constraints: bool = False
    must_finish_by: date | None = None
    deadline_answer: str | None = None
    specialist_answer: str | None = None
    contractor_rate_cents: int | None = None
    specialist_rate_confirmed: bool = False
    team: Sequence[str] = ()
    team_from_project: bool = False
    # Team members who are employees (not contractors), and those of them with no
    # company rate on record, costed at the standard rate.
    employees: Sequence[str] = ()
    standard_rate_employees: Sequence[str] = ()
    capacity_mode: str = "soft"
    skills: Sequence[SkillFact] = ()
    task_skill_gaps: Sequence[str] = ()
    skill_mode: str = "hard"
    contingency_pct: float = 0.0
    working_days_per_week: int = 5
    milestones: Sequence[MilestoneFact] = ()

    @property
    def skill_gaps(self) -> list[str]:
        """Required skills nobody on the team has (project list first, then work packages)."""
        gaps = [skill.name for skill in self.skills if skill.required and not skill.covered]
        return gaps + [name for name in self.task_skill_gaps if name not in gaps]


def _task_skill_gaps(inp: PlanningInput) -> list[str]:
    gaps: list[str] = []
    for task in inp.tasks:
        if not task.skills or any(covers(employee.skills, task) for employee in inp.employees):
            continue
        gaps += [
            requirement.skill_name
            for requirement in task.skills
            if requirement.skill_name not in gaps
        ]
    return gaps


def facts_from_input(inp: PlanningInput, **database_facts: Any) -> InputFacts:
    """Facts the planning input already holds, plus the ones the repository looked up."""
    constraints = inp.constraints
    facts = InputFacts(
        work_packages=len(inp.tasks),
        effort_days=sum(task.duration_days for task in inp.tasks),
        dependency_types=dict(Counter(dependency.type for dependency in inp.dependencies)),
        budget_cents=constraints.budget_total_cents,
        budget_mode=constraints.budget_mode,
        must_finish_by=constraints.must_finish_by,
        contractor_rate_cents=constraints.contractor_rate_cents,
        team=[employee.name for employee in inp.employees],
        employees=[employee.name for employee in inp.employees if not employee.is_contractor],
        standard_rate_employees=[
            employee.name
            for employee in inp.employees
            if not employee.is_contractor and employee.hourly_rate_cents is None
        ],
        capacity_mode=constraints.capacity_mode,
        task_skill_gaps=_task_skill_gaps(inp),
        skill_mode=constraints.skill_mode,
        working_days_per_week=constraints.working_days_per_week,
        milestones=[
            MilestoneFact(milestone.name, milestone.due_date, milestone.mode)
            for milestone in inp.milestones
        ],
    )
    return replace(facts, **database_facts)


def quote_states_amount(quote: str, cents: int) -> bool:
    """Whether a notes quote names this whole-dollar amount ("$375K", "$375,000")."""
    dollars = cents // 100
    compact = quote.lower().replace(",", "").replace(" ", "").replace("million", "m")
    return any(
        round(float(number) * _UNIT.get(unit, 1)) == dollars
        for number, unit in _AMOUNT.findall(compact)
    )


def inputs_hash(inp: PlanningInput) -> str:
    """SHA-256 of what the solver will use (other projects' bookings and today excluded).

    Hours, type, rates and skills of every person are part of it, so editing
    any of them invalidates a confirmed sheet.
    """
    snapshot = inp.to_snapshot()
    snapshot.pop("allocations", None)
    # The day it is computed isn't an input: the hash must not change overnight.
    snapshot.pop("today", None)
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
