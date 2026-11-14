"""Which work packages need each of the project's skills (round-2 decision D6).

Purpose: The project's skill list (step 5) reaches the optimizer through the work packages it is
linked to: those whose WBS skills (step 4) already include it, else those whose name or
description mention every word of the skill name ("Okta SSO" matches "SSO with Okta"), at
proficiency 3 (`LINKED_PROFICIENCY`). Only Required skills limit who may do the work (RES-011,
CON-010): an Optional skill never blocks a package, slows anyone down or calls in a specialist.
How it fits: `link_skills` is called by app/repositories/planning_input.py; unlinked Required
skills are reported on the confirmation sheet; layer: optimization.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.optimization.model import SkillRequirement

LINKED_PROFICIENCY = 3
_WORD = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class ProjectSkill:
    """A skill on the project's list (step 5)."""

    skill_id: str
    name: str
    required: bool


@dataclass(frozen=True, slots=True)
class PackageText:
    """What a work package says about itself: its name and description."""

    id: str
    name: str
    description: str | None = None


def _words(text: str) -> frozenset[str]:
    """Return the casefolded words of a text."""
    return frozenset(_WORD.findall(text.casefold()))


def mentions(skill_name: str, package: PackageText) -> bool:
    """Whether the package's name or description contains every word of the skill name."""
    wanted = _words(skill_name)
    return bool(wanted) and wanted <= _words(f"{package.name} {package.description or ''}")


def link_skills(
    packages: Sequence[PackageText],
    project_skills: Sequence[ProjectSkill],
    tagged: Mapping[str, Sequence[SkillRequirement]],
) -> dict[str, list[SkillRequirement]]:
    """Each package's skill requirements: its WBS skills (`tagged`, package id ->
    requirements) other than the project's Optional skills, plus the keyword links of
    the Required project skills no package is tagged with."""
    optional = {skill.skill_id for skill in project_skills if not skill.required}
    linked = {
        package_id: [
            requirement for requirement in requirements if requirement.skill_id not in optional
        ]
        for package_id, requirements in tagged.items()
    }
    in_use = {
        requirement.skill_id for requirements in tagged.values() for requirement in requirements
    }
    for skill in project_skills:
        if not skill.required or skill.skill_id in in_use:
            continue
        for package in packages:
            if mentions(skill.name, package):
                linked.setdefault(package.id, []).append(
                    SkillRequirement(skill.skill_id, skill.name, LINKED_PROFICIENCY)
                )
    return linked
