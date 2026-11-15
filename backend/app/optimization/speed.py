"""How fast a person does a work package at their skill level (round-2 decision D5).

Purpose: A more skilled person finishes sooner and costs less, a less skilled one the reverse:
    * a package's WBS duration and effort are for someone at its minimum proficiency;
    * each level above it is 10% faster, at most 30% (3 levels); the weakest required skill
      sets the pace;
    * each level below it is 25% slower (only "Can flex" skills let such a person take it);
    * the pace scales the hours of work and combines with the part-time stretch;
    * contractor profiles work at the WBS pace: they stand for whoever is hired.
How it fits: Used by load.py, problem.py and the report (explain.py, report_context.py);
layer: optimization.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from app.optimization.model import SkillRequirement, Task
from app.optimization.outcome import Resource

WBS_PACE = 100  # percent of the WBS effort
FASTER_PER_LEVEL = 10
MAX_FASTER = 30
SLOWER_PER_LEVEL = 25


def _margin(resource: Resource, requirement: SkillRequirement) -> int:
    """Return how many levels the person is above (positive) or below the skill's minimum."""
    return resource.proficiency(requirement.skill_id) - requirement.min_proficiency


def pace_percent(resource: Resource, task: Task) -> int:
    """Percent of the WBS effort this person needs for the task (70 at best, 100 = WBS pace)."""
    if resource.kind != "employee" or not task.skills:
        return WBS_PACE
    gap = resource.skill_gap(task)
    if gap:
        return WBS_PACE + SLOWER_PER_LEVEL * gap
    surplus = min(_margin(resource, requirement) for requirement in task.skills)
    return WBS_PACE - min(FASTER_PER_LEVEL * surplus, MAX_FASTER)


def work_hours(task: Task, resource: Resource) -> float:
    """Hours of work the task takes this person: its remaining effort at their pace."""
    return task.hours * pace_percent(resource, task) / WBS_PACE


def pace_level(resource: Resource, task: Task) -> int | None:
    """The person's level in the skill that sets their pace (None at the WBS pace)."""
    if pace_percent(resource, task) == WBS_PACE:
        return None
    deciding = min(task.skills, key=lambda requirement: _margin(resource, requirement))
    return resource.proficiency(deciding.skill_id)
