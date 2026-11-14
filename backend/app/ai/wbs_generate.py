"""WBS generation: the Claude prompt and output schema, a template fallback and owner suggestions.

Purpose: Turn accepted scope into a three-level WBS the PM then edits: Claude's answer is clamped to
what the schema accepts and dangling references are dropped; without Claude, one deliverable per
accepted deliverable item with generic work packages; owners are suggested by skill match.
How it fits: Pure functions; the AI job (app/jobs/ai_jobs.py) reads the input through
app/repositories/ai_jobs.py and writes the plan with app/repositories/ai_wbs_writer.py; layer: ai.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.prompting import DATA_RULE, as_data, data_block, one_line
from app.domain.common import DependencyType, ScopeCategory, ScopeStatus

DAYS_PER_WEEK = 5
MIN_PROFICIENCY = 2
MAX_DELIVERABLES = 12
MAX_PACKAGES = 10
MAX_SKILLS = 4
TEMPLATE_NAME_LIMIT = 80
NAME_LIMIT = 300
DESCRIPTION_LIMIT = 2000
SKILL_LIMIT = 100

NO_DELIVERABLES = "Accept at least one deliverable in Scope Review first."
NO_SCOPE = "Accept at least one scope item in Scope Review first."

# (name, weeks) of the generic work packages under each template deliverable.
TEMPLATE_PACKAGES: tuple[tuple[str, int], ...] = (
    ("Design & planning", 2),
    ("Build", 4),
    ("Test & acceptance", 2),
)


class NothingToPlanError(ValueError):
    """The project has no scope to build a WBS from; the message is user-facing."""


# --------------------------------------------------------------------------- inputs
@dataclass(frozen=True, slots=True)
class ScopeEntry:
    """A scope item as generation reads it."""

    id: uuid.UUID
    category: ScopeCategory
    text: str
    status: ScopeStatus


@dataclass(frozen=True, slots=True)
class TeamMember:
    """A person who may own work packages, with skill name -> proficiency."""

    id: uuid.UUID
    full_name: str
    role_title: str | None
    skills: Mapping[str, int] = field(default_factory=dict)  # skill name -> proficiency


@dataclass(frozen=True, slots=True)
class GenerationInput:
    """Everything WBS generation reads about a project."""

    project_name: str
    description: str | None
    project_type: str
    methodology: str
    start_date: date | None
    target_end_date: date | None
    scope: tuple[ScopeEntry, ...]
    org_skills: tuple[str, ...]
    team: tuple[TeamMember, ...]


# --------------------------------------------------------------------------- plan
@dataclass(frozen=True, slots=True)
class PlannedPackage:
    """A work package of a generated plan, keyed so links can refer to it."""

    key: str
    name: str
    description: str | None
    duration_days: int
    skills: tuple[str, ...]
    scope_item_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class PlannedDeliverable:
    """A deliverable of a generated plan and its work packages."""

    name: str
    description: str | None
    scope_item_id: uuid.UUID | None
    packages: tuple[PlannedPackage, ...]


@dataclass(frozen=True, slots=True)
class PlannedDependency:
    """A link between two planned work packages, by key."""

    predecessor: str
    successor: str
    type: DependencyType = DependencyType.FS
    lag_days: int = 0


@dataclass(frozen=True, slots=True)
class WbsPlan:
    """A generated WBS: deliverables with work packages, and the links between packages."""

    deliverables: tuple[PlannedDeliverable, ...]
    dependencies: tuple[PlannedDependency, ...]

    @property
    def packages(self) -> list[PlannedPackage]:
        """Return every work package of the plan, in order."""
        return [package for deliverable in self.deliverables for package in deliverable.packages]

    @property
    def empty(self) -> bool:
        """Tell whether the plan has no work packages."""
        return not self.packages


def select_scope(
    items: Iterable[ScopeEntry], categories: set[ScopeCategory] | None = None
) -> list[ScopeEntry]:
    """Accepted items; when none are accepted yet, every item not removed."""
    pool = [item for item in items if categories is None or item.category in categories]
    accepted = [item for item in pool if item.status == ScopeStatus.ACCEPTED]
    return accepted or [item for item in pool if item.status != ScopeStatus.REMOVED]


def template_plan(generation_input: GenerationInput) -> WbsPlan:
    """One deliverable per deliverable scope item, each with generic work packages."""
    items = select_scope(generation_input.scope, {ScopeCategory.DELIVERABLE})
    if not items:
        raise NothingToPlanError(NO_DELIVERABLES)
    deliverables: list[PlannedDeliverable] = []
    dependencies: list[PlannedDependency] = []
    for deliverable_index, item in enumerate(items[:MAX_DELIVERABLES], start=1):
        name = (
            one_line(item.text, TEMPLATE_NAME_LIMIT).rstrip(".")
            or f"Deliverable {deliverable_index}"
        )
        packages = tuple(
            PlannedPackage(
                key=f"D{deliverable_index}.{package_index}",
                name=package_name,
                description=None,
                duration_days=weeks * DAYS_PER_WEEK,
                skills=(),
                scope_item_id=item.id,
            )
            for package_index, (package_name, weeks) in enumerate(TEMPLATE_PACKAGES, start=1)
        )
        dependencies.extend(
            PlannedDependency(first.key, second.key) for first, second in pairwise(packages)
        )
        deliverables.append(PlannedDeliverable(name, None, item.id, packages))
    return WbsPlan(tuple(deliverables), tuple(dependencies))


# --------------------------------------------------------------------------- Claude
# Claude's structured-output schema. The classes deliberately have no docstrings:
# Pydantic would send them to the model as schema descriptions and change its answers.
class GeneratedPackage(BaseModel):
    key: str = Field(description="Unique short id for this work package, e.g. WP1")
    name: str = Field(description="Work package name, at most 80 characters")
    description: str = Field(description="One sentence; may be empty")
    duration_weeks: float = Field(description="Elapsed duration in weeks (0.5 to 12)")
    skills: list[str] = Field(description="1-3 skills needed, reusing existing skill names")
    scope_ref: str | None = Field(description="Scope item id (S1, S2, ...) it covers, or null")


class GeneratedDeliverable(BaseModel):
    name: str = Field(description="Deliverable name, at most 80 characters")
    description: str = Field(description="One sentence; may be empty")
    scope_ref: str | None = Field(description="Scope item id (S1, S2, ...) it covers, or null")
    work_packages: list[GeneratedPackage]


class GeneratedDependency(BaseModel):
    predecessor: str = Field(description="Work package key that must happen first")
    successor: str = Field(description="Work package key that depends on it")
    type: Literal["FS", "SS", "FF", "SF"]
    lag_days: int


class GeneratedWbs(BaseModel):
    deliverables: list[GeneratedDeliverable]
    dependencies: list[GeneratedDependency]


GENERATE_SYSTEM = (
    "You are a senior project planner. Build a three-level work breakdown structure (WBS) "
    "for the project described in the user message: the project itself is level 1, "
    "deliverables are level 2 and work packages are level 3. "
    + DATA_RULE
    + " Rules: 3 to 8 deliverables that together cover the accepted scope; 2 to 6 work "
    "packages per deliverable, each a concrete piece of work lasting 0.5 to 12 weeks; "
    "names are short noun phrases (at most 80 characters) without numbering; include "
    "testing/acceptance, deployment and handover work where the project needs it; reuse "
    "the organization's existing skill names when they fit and list 1 to 3 skills per "
    "work package; set scope_ref to the id (S1, S2, ...) of the scope item a deliverable "
    "or work package covers, or null; add finish-to-start dependencies (lag 0 unless the "
    "data says otherwise) only between work package keys where one really needs the other, "
    "never creating a cycle. Do not invent facts, names or figures the data doesn't support."
)


def build_generation_prompt(generation_input: GenerationInput) -> tuple[str, dict[str, uuid.UUID]]:
    """Return the user prompt and the scope reference ids (S1, S2, ...) it uses."""
    items = select_scope(generation_input.scope)
    refs = {f"S{index}": item.id for index, item in enumerate(items, start=1)}
    lines = [
        f"Project: {as_data(one_line(generation_input.project_name, 200))}",
        f"Type: {generation_input.project_type}; methodology: {generation_input.methodology}",
    ]
    if generation_input.start_date or generation_input.target_end_date:
        start, end = (
            generation_input.start_date or "unknown",
            generation_input.target_end_date or "unknown",
        )
        lines.append(f"Dates: start {start}, target end {end}")
    if generation_input.description:
        lines.append(f"Description: {as_data(one_line(generation_input.description, 2000))}")
    lines.append("Scope items:")
    lines.extend(
        f"{ref} [{item.category}] {as_data(one_line(item.text, 500))}"
        for ref, item in zip(refs, items, strict=True)
    )
    if generation_input.org_skills:
        skills = ", ".join(
            as_data(one_line(skill, SKILL_LIMIT)) for skill in generation_input.org_skills[:80]
        )
        lines.append(f"Existing organization skills: {skills}")
    return data_block("\n".join(lines)) + "\n\nCreate the WBS.", refs


def _weeks_to_days(weeks: float) -> int:
    """Return weeks as working days, clamped to 1..3650 (a week when the value isn't finite)."""
    if not math.isfinite(weeks):
        return DAYS_PER_WEEK
    return max(1, min(3650, round(weeks * DAYS_PER_WEEK)))


def _skills(names: Sequence[str]) -> tuple[str, ...]:
    """Return up to `MAX_SKILLS` cleaned skill names without case-insensitive duplicates."""
    seen: dict[str, str] = {}
    for raw in names:
        name = one_line(raw, SKILL_LIMIT)
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name
    return tuple(seen.values())[:MAX_SKILLS]


def _text(value: str, limit: int) -> str | None:
    """Return the text on one line within `limit`, or None when it is empty."""
    cleaned = one_line(value, limit)
    return cleaned or None


def plan_from_model(generated: GeneratedWbs, refs: Mapping[str, uuid.UUID]) -> WbsPlan:
    """Clamp Claude's output to what the schema accepts and drop dangling references."""
    keys: dict[str, str] = {}
    deliverables: list[PlannedDeliverable] = []
    for deliverable in generated.deliverables[:MAX_DELIVERABLES]:
        name = _text(deliverable.name, NAME_LIMIT)
        if name is None:
            continue
        packages: list[PlannedPackage] = []
        for package in deliverable.work_packages[:MAX_PACKAGES]:
            package_name = _text(package.name, NAME_LIMIT)
            if package_name is None:
                continue
            key = f"WP{len(keys) + 1}"
            keys.setdefault(package.key.strip(), key)
            packages.append(
                PlannedPackage(
                    key=key,
                    name=package_name,
                    description=_text(package.description, DESCRIPTION_LIMIT),
                    duration_days=_weeks_to_days(package.duration_weeks),
                    skills=_skills(package.skills),
                    scope_item_id=refs.get((package.scope_ref or "").strip()),
                )
            )
        deliverables.append(
            PlannedDeliverable(
                name=name,
                description=_text(deliverable.description, DESCRIPTION_LIMIT),
                scope_item_id=refs.get((deliverable.scope_ref or "").strip()),
                packages=tuple(packages),
            )
        )

    dependencies: list[PlannedDependency] = []
    pairs: set[frozenset[str]] = set()
    for link in generated.dependencies:
        predecessor, successor = (
            keys.get(link.predecessor.strip()),
            keys.get(link.successor.strip()),
        )
        if (
            predecessor is None
            or successor is None
            or predecessor == successor
            or frozenset((predecessor, successor)) in pairs
        ):
            continue
        pairs.add(frozenset((predecessor, successor)))
        lag = max(-365, min(365, link.lag_days))
        dependencies.append(
            PlannedDependency(predecessor, successor, DependencyType(link.type), lag)
        )
    return WbsPlan(tuple(deliverables), tuple(dependencies))


# --------------------------------------------------------------------------- owners
def suggest_owners(
    packages: Sequence[PlannedPackage],
    team: Sequence[TeamMember],
    min_proficiency: int = MIN_PROFICIENCY,
) -> dict[str, uuid.UUID]:
    """Package key -> the team member covering most of its skills.

    A member covers a skill when their proficiency meets the requirement
    written for it (`min_proficiency`). Ties go to whoever has the fewest
    packages suggested so far, then to the name. Packages nobody covers (or
    without skills) get no suggestion.
    """
    held = {
        teammate.id: {
            name.casefold() for name, level in teammate.skills.items() if level >= min_proficiency
        }
        for teammate in team
    }
    load = dict.fromkeys(held, 0)
    owners: dict[str, uuid.UUID] = {}
    for package in packages:
        required = {skill.casefold() for skill in package.skills}
        if not required:
            continue
        best: tuple[tuple[int, int, str, str], uuid.UUID] | None = None
        for member in team:
            cover = len(required & held[member.id])
            if cover == 0:
                continue
            rank = (-cover, load[member.id], member.full_name.casefold(), str(member.id))
            if best is None or rank < best[0]:
                best = (rank, member.id)
        if best is not None:
            owners[package.key] = best[1]
            load[best[1]] += 1
    return owners
