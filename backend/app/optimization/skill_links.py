"""Which work packages need each of the project's skills (round-2 decision D6).

The project's skill list (step 5) reaches the optimizer through the work
packages it is linked to:

* the packages whose WBS skills (tagged on step 4) already include it, else
* the packages whose name or description mention it: every word of the
  skill name, case-insensitive, in any order ("Okta SSO" matches "SSO with
  Okta"). Such a link asks for proficiency 3 (`LINKED_PROFICIENCY`), the
  level a skill tagged on step 4 or given to a person on step 5 gets.

Only Required skills are linked by keyword: an Optional skill doesn't limit
who may do the work. A Required skill linked to no package is reported on
the confirmation sheet (`inputs.InputFacts.unlinked_skills`).
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
    requirements) plus the keyword links of the Required project skills no package
    is tagged with."""
    linked = {package_id: list(requirements) for package_id, requirements in tagged.items()}
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
