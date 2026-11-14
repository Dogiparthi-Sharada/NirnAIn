"""Shapes of the demo seed's data and the demo calendar.

Purpose: The mockup's walkthrough is dated 2025, but the optimizer plans from today, so every
date moves to a year that is still ahead (same month and day as the mockup, so the screens
read like it). Plain frozen dataclasses keep the flow code generic: one function seeds any
`ProjectSpec`, whatever its stage.
How it fits: `seed_walkthrough_data.py` and `seed_portfolio_data.py` build `ProjectSpec`s from
a `DemoCalendar`; `seed_flows.py` turns them into API calls; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

Stage = Literal["draft", "planning", "applied"]
WORKING_DAYS_PER_WEEK = 5
# The walkthrough starts on Jan 15 (the mockup's kickoff date) of the demo year.
WALKTHROUGH_START = (1, 15)


@dataclass(frozen=True)
class DemoCalendar:
    """`today` (what the optimizer plans from) and `year` (the walkthrough's year)."""

    today: date
    year: int

    @classmethod
    def starting(cls, today: date, year: int | None = None) -> DemoCalendar:
        """The first year whose Jan 15 is not in the past, unless a year is given."""
        if year is None:
            year = today.year if today <= date(today.year, *WALKTHROUGH_START) else today.year + 1
        return cls(today=today, year=year)

    def on(self, month: int, day: int, years_later: int = 0) -> date:
        """A mockup month/day in the demo year (the mockup's 2025)."""
        return date(self.year + years_later, month, day)

    def monday(self, weeks_from_now: int) -> date:
        """The Monday `weeks_from_now` weeks away (negative: projects already running)."""
        return self.today - timedelta(days=self.today.weekday()) + timedelta(weeks=weeks_from_now)

    @property
    def today_label(self) -> str:
        """Today as meeting notes write it, e.g. 'November 14, 2026'."""
        return f"{self.today:%B} {self.today.day}, {self.today.year}"


@dataclass(frozen=True)
class Person:
    """A team member; a rate makes them a contractor, otherwise an employee at the company rate."""

    name: str
    role: str
    hours_per_week: int
    skills: tuple[str, ...] = ()
    contractor_rate_cents: int | None = None


@dataclass(frozen=True)
class WorkPackage:
    """A level-3 WBS node. `code` is the mockup's number, used to refer to it in links."""

    code: str
    name: str
    weeks: float
    owner: str | None = None
    skills: tuple[str, ...] = ()

    @property
    def duration_days(self) -> int:
        """Weeks as whole working days (5 a week)."""
        return round(self.weeks * WORKING_DAYS_PER_WEEK)


@dataclass(frozen=True)
class Deliverable:
    """A level-2 WBS node and its work packages."""

    code: str
    name: str
    packages: tuple[WorkPackage, ...]


@dataclass(frozen=True)
class Link:
    """A dependency between two work packages (codes); FS = finish-to-start, SS = start-to-start."""

    predecessor: str
    successor: str
    kind: str = "FS"


@dataclass(frozen=True)
class Milestone:
    """A dated milestone on a deliverable (code); soft unless `hard`."""

    name: str
    due: date
    deliverable: str | None = None
    hard: bool = False


@dataclass(frozen=True)
class Limits:
    """Step 5 limits and the answers to its two questions (None = not answered yet)."""

    budget_cents: int
    start: date
    must_finish_by: date
    contingency_pct: float = 0.0
    labor_cap_cents: int | None = None
    infra_cap_cents: int | None = None
    budget_mode: str = "hard"
    capacity_mode: str = "hard"
    skill_mode: str = "hard"
    specialist_answer: str | None = None
    deadline_answer: str | None = None
    contractor_rate_cents: int | None = None
    milestones: tuple[Milestone, ...] = ()


@dataclass(frozen=True)
class ChangeSpec:
    """A step-7 change recorded from meeting notes, re-planned and approved."""

    label: str
    notes: str
    # Which detected change must be there (kind, words in its target), so a silent mis-read
    # fails the seed.
    expect_kind: str
    expect_target: str
    # The same change as a direct edit (person, new hours per week), used when AI calls are skipped.
    edit_hours: tuple[str, int]


@dataclass(frozen=True)
class ProjectSpec:
    """Everything one demo project needs, from step 1 up to its `stage`."""

    name: str
    description: str
    project_type: str
    department: str
    sponsor: str
    start: date
    due: date
    budget_cents: int
    stage: Stage
    priority: str = "medium"
    methodology: str = "waterfall"
    intake_notes: str | None = None
    # (category, text) of extracted items the PM removes on Scope Review (e.g. duplicates).
    remove_scope: tuple[tuple[str, str], ...] = ()
    # Risks the PM adds by hand on Scope Review (shown on the dashboard).
    manual_risks: tuple[str, ...] = ()
    team: tuple[Person, ...] = ()
    skills: tuple[tuple[str, bool], ...] = ()  # (name, required)
    deliverables: tuple[Deliverable, ...] = ()
    links: tuple[Link, ...] = ()
    limits: Limits | None = None
    validate_wbs: bool = False
    status_after_apply: str | None = None
    progress_pct: int = 0
    changes: tuple[ChangeSpec, ...] = field(default=())

    @property
    def packages(self) -> list[WorkPackage]:
        """All work packages, in WBS order."""
        return [package for deliverable in self.deliverables for package in deliverable.packages]
