"""WBS generation: Claude prompt + output schema, a deterministic template
fallback, and owner suggestions. Pure functions; the worker does the I/O."""

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
    id: uuid.UUID
    category: ScopeCategory
    text: str
    status: ScopeStatus


@dataclass(frozen=True, slots=True)
class TeamMember:
    id: uuid.UUID
    full_name: str
    role_title: str | None
    skills: Mapping[str, int] = field(default_factory=dict)  # skill name -> proficiency


@dataclass(frozen=True, slots=True)
class GenerationInput:
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
    key: str
    name: str
    description: str | None
    duration_days: int
    skills: tuple[str, ...]
    scope_item_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class PlannedDeliverable:
    name: str
    description: str | None
    scope_item_id: uuid.UUID | None
    packages: tuple[PlannedPackage, ...]


@dataclass(frozen=True, slots=True)
class PlannedDependency:
    predecessor: str
    successor: str
    type: DependencyType = DependencyType.FS
    lag_days: int = 0


@dataclass(frozen=True, slots=True)
class WbsPlan:
    deliverables: tuple[PlannedDeliverable, ...]
    dependencies: tuple[PlannedDependency, ...]

    @property
    def packages(self) -> list[PlannedPackage]:
        return [p for d in self.deliverables for p in d.packages]

    @property
    def empty(self) -> bool:
        return not self.packages


def select_scope(
    items: Iterable[ScopeEntry], categories: set[ScopeCategory] | None = None
) -> list[ScopeEntry]:
    """Accepted items; when none are accepted yet, every item not removed."""
    pool = [i for i in items if categories is None or i.category in categories]
    accepted = [i for i in pool if i.status == ScopeStatus.ACCEPTED]
    return accepted or [i for i in pool if i.status != ScopeStatus.REMOVED]


def template_plan(inp: GenerationInput) -> WbsPlan:
    """One deliverable per deliverable scope item, each with generic work packages."""
    items = select_scope(inp.scope, {ScopeCategory.DELIVERABLE})
    if not items:
        raise NothingToPlanError(NO_DELIVERABLES)
    deliverables: list[PlannedDeliverable] = []
    dependencies: list[PlannedDependency] = []
    for di, item in enumerate(items[:MAX_DELIVERABLES], start=1):
        name = one_line(item.text, TEMPLATE_NAME_LIMIT).rstrip(".") or f"Deliverable {di}"
        packages = tuple(
            PlannedPackage(
                key=f"D{di}.{pi}",
                name=pkg_name,
                description=None,
                duration_days=weeks * DAYS_PER_WEEK,
                skills=(),
                scope_item_id=item.id,
            )
            for pi, (pkg_name, weeks) in enumerate(TEMPLATE_PACKAGES, start=1)
        )
        dependencies.extend(PlannedDependency(a.key, b.key) for a, b in pairwise(packages))
        deliverables.append(PlannedDeliverable(name, None, item.id, packages))
    return WbsPlan(tuple(deliverables), tuple(dependencies))


# --------------------------------------------------------------------------- Claude
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


def build_generation_prompt(inp: GenerationInput) -> tuple[str, dict[str, uuid.UUID]]:
    """Return the user prompt and the scope reference ids (S1, S2, ...) it uses."""
    items = select_scope(inp.scope)
    refs = {f"S{i}": item.id for i, item in enumerate(items, start=1)}
    lines = [
        f"Project: {as_data(one_line(inp.project_name, 200))}",
        f"Type: {inp.project_type}; methodology: {inp.methodology}",
    ]
    if inp.start_date or inp.target_end_date:
        start, end = inp.start_date or "unknown", inp.target_end_date or "unknown"
        lines.append(f"Dates: start {start}, target end {end}")
    if inp.description:
        lines.append(f"Description: {as_data(one_line(inp.description, 2000))}")
    lines.append("Scope items:")
    lines.extend(
        f"{ref} [{item.category}] {as_data(one_line(item.text, 500))}"
        for ref, item in zip(refs, items, strict=True)
    )
    if inp.org_skills:
        skills = ", ".join(as_data(one_line(s, SKILL_LIMIT)) for s in inp.org_skills[:80])
        lines.append(f"Existing organization skills: {skills}")
    return data_block("\n".join(lines)) + "\n\nCreate the WBS.", refs


def _weeks_to_days(weeks: float) -> int:
    if not math.isfinite(weeks):
        return DAYS_PER_WEEK
    return max(1, min(3650, round(weeks * DAYS_PER_WEEK)))


def _skills(names: Sequence[str]) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for raw in names:
        name = one_line(raw, SKILL_LIMIT)
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name
    return tuple(seen.values())[:MAX_SKILLS]


def _text(value: str, limit: int) -> str | None:
    cleaned = one_line(value, limit)
    return cleaned or None


def plan_from_model(gen: GeneratedWbs, refs: Mapping[str, uuid.UUID]) -> WbsPlan:
    """Clamp Claude's output to what the schema accepts and drop dangling references."""
    keys: dict[str, str] = {}
    deliverables: list[PlannedDeliverable] = []
    for d in gen.deliverables[:MAX_DELIVERABLES]:
        name = _text(d.name, NAME_LIMIT)
        if name is None:
            continue
        packages: list[PlannedPackage] = []
        for p in d.work_packages[:MAX_PACKAGES]:
            pname = _text(p.name, NAME_LIMIT)
            if pname is None:
                continue
            key = f"WP{len(keys) + 1}"
            keys.setdefault(p.key.strip(), key)
            packages.append(
                PlannedPackage(
                    key=key,
                    name=pname,
                    description=_text(p.description, DESCRIPTION_LIMIT),
                    duration_days=_weeks_to_days(p.duration_weeks),
                    skills=_skills(p.skills),
                    scope_item_id=refs.get((p.scope_ref or "").strip()),
                )
            )
        deliverables.append(
            PlannedDeliverable(
                name=name,
                description=_text(d.description, DESCRIPTION_LIMIT),
                scope_item_id=refs.get((d.scope_ref or "").strip()),
                packages=tuple(packages),
            )
        )

    dependencies: list[PlannedDependency] = []
    pairs: set[frozenset[str]] = set()
    for dep in gen.dependencies:
        pred, succ = keys.get(dep.predecessor.strip()), keys.get(dep.successor.strip())
        if pred is None or succ is None or pred == succ or frozenset((pred, succ)) in pairs:
            continue
        pairs.add(frozenset((pred, succ)))
        lag = max(-365, min(365, dep.lag_days))
        dependencies.append(PlannedDependency(pred, succ, DependencyType(dep.type), lag))
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
        m.id: {name.casefold() for name, level in m.skills.items() if level >= min_proficiency}
        for m in team
    }
    load = dict.fromkeys(held, 0)
    owners: dict[str, uuid.UUID] = {}
    for package in packages:
        required = {s.casefold() for s in package.skills}
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
