"""Who can do the work: the team as priced resources, plus contractor profiles.

Purpose: Apply round-2 decisions D1, D2 and D6 to the people the solver may assign:
    * team members are priced at their own rate, a contractor without one at the project's
      contractor rate, anyone else at the standard rate; overtime at 1.5x an employee's cost;
    * skill gaps: with contractors allowed and a rate, each group of packages needing skills
      nobody on the team has gets one specialist profile ("Contractor: Okta SSO specialist");
    * late work: "Bring in a contractor" adds one generic contractor for any other package;
    * a contractor profile stands for as many people as needed (no capacity limit), and each
      package has at most one contractor candidate, so the solver never chooses between equals.
How it fits: `build_resources` is called by problem.py; costing.py prices their hours;
layer: optimization.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Sequence

from app.optimization.model import DEFAULT_HOURLY_RATE_CENTS, Employee, PlanningInput, Task
from app.optimization.outcome import Resource, qualified

EMPLOYEE_OVERTIME_MULTIPLIER = 1.5
CONTRACTOR_WEEKLY_HOURS = 40.0
CONTRACTOR_ID = "contractor"
CONTRACTOR_NAME = "Contractor"
SPECIALIST_ID_PREFIX = "contractor:"
MAX_PROFICIENCY = 5


def planned_rate_cents(employee: Employee, contractor_rate_cents: int | None = None) -> int:
    """The hourly rate the optimizer plans this person at.

    Their own rate (an employee's company rate, a contractor's contracted one);
    else, for a contractor, the project's contractor rate; else the standard rate.
    """
    if employee.hourly_rate_cents is not None:
        return employee.hourly_rate_cents
    if employee.is_contractor and contractor_rate_cents is not None:
        return contractor_rate_cents
    return DEFAULT_HOURLY_RATE_CENTS


def overtime_rate_cents(employee: Employee, rate_cents: int) -> int:
    """What an hour above their capacity costs: 1.5x for employees, the rate for contractors."""
    if employee.is_contractor:
        return rate_cents
    return round(rate_cents * EMPLOYEE_OVERTIME_MULTIPLIER)


def uncovered(task: Task, team: Sequence[Resource]) -> bool:
    """Whether `task` needs skills no single team member fully has."""
    return bool(task.skills) and not any(qualified(member, task) for member in team)


def contractor_for(task: Task, team: Sequence[Resource]) -> str:
    """Id of the one contractor profile that may take `task`: the specialist for its
    skills when nobody on the team has them, else the generic contractor."""
    if uncovered(task, team):
        return SPECIALIST_ID_PREFIX + "+".join(
            sorted(requirement.skill_id for requirement in task.skills)
        )
    return CONTRACTOR_ID


def _team(planning_input: PlanningInput) -> list[Resource]:
    """Return the project team as priced employee resources."""
    rate_default = planning_input.constraints.contractor_rate_cents
    team = []
    for employee in planning_input.employees:
        rate = planned_rate_cents(employee, rate_default)
        team.append(
            Resource(
                id=employee.id,
                name=employee.name,
                kind="employee",
                weekly_capacity_hours=employee.weekly_capacity_hours,
                rate_cents=rate,
                skills=dict(employee.skills),
                employee_id=employee.id,
                overtime_rate_cents=overtime_rate_cents(employee, rate),
            )
        )
    return team


def _contractor(resource_id: str, name: str, rate: int, skills: dict[str, int]) -> Resource:
    """Return a contractor profile resource: no capacity limit, overtime at its own rate."""
    return Resource(
        id=resource_id,
        name=name,
        kind="contractor",
        weekly_capacity_hours=CONTRACTOR_WEEKLY_HOURS,
        rate_cents=rate,
        skills=skills,
        overtime_rate_cents=rate,
    )


def build_resources(planning_input: PlanningInput) -> tuple[Resource, ...]:
    """The team, then the specialist profiles for skill gaps, then the generic contractor.

    Contractor profiles exist only with `allow_contractors` and a contractor
    rate; the generic one also needs `contractor_any_work`.
    """
    constraints = planning_input.constraints
    team = _team(planning_input)
    rate = constraints.contractor_rate_cents
    if not constraints.allow_contractors or rate is None:
        return tuple(team)
    work = [task for task in planning_input.tasks if not task.done]
    specialists: dict[str, dict[str, str]] = {}  # profile id -> skill id -> skill name
    for task in work:
        if uncovered(task, team):
            names = specialists.setdefault(contractor_for(task, team), {})
            names.update(
                {requirement.skill_id: requirement.skill_name for requirement in task.skills}
            )
    contractors = [
        _contractor(
            profile_id,
            f"Contractor: {' / '.join(names[skill_id] for skill_id in sorted(names))} specialist",
            rate,
            dict.fromkeys(names, MAX_PROFICIENCY),
        )
        for profile_id, names in specialists.items()
    ]
    if constraints.contractor_any_work:
        every_skill = {
            requirement.skill_id: MAX_PROFICIENCY for task in work for requirement in task.skills
        }
        contractors.append(_contractor(CONTRACTOR_ID, CONTRACTOR_NAME, rate, every_skill))
    return (*team, *contractors)


def candidates_for(
    work: Sequence[Task], resources: Sequence[Resource]
) -> dict[str, list[Resource]]:
    """Who may ever take each task: every team member (skills are checked by the model)
    and the task's contractor profile, if there is one (`contractor_for`)."""
    team = [resource for resource in resources if resource.kind == "employee"]
    contractors = {resource.id: resource for resource in resources if resource.kind != "employee"}
    found = {}
    for task in work:
        contractor = contractors.get(contractor_for(task, team))
        found[task.id] = team + ([contractor] if contractor is not None else [])
    return found
