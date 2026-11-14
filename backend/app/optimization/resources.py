"""Who can do the work: the team as priced resources, plus contractor profiles.

Modelling rules (round-2 decisions D1, D2 and D6):

* Team members are priced at their own rate (an employee's company rate, a
  contractor's contracted one); a contractor without one at the project's
  contractor rate; anyone else at the standard rate. Overtime is billed at
  1.5x an employee's cost and at a contractor's own rate.
* Skill gaps: with contractors allowed and a contractor rate (the specialist
  answer Yes plus a confirmed rate), each group of work packages needing
  skills nobody on the team fully has gets one specialist profile, e.g.
  "Contractor: Okta SSO specialist", with proficiency 5 in those skills.
* Late work: the "Bring in a contractor" option
  (`Constraints.contractor_any_work`) adds one generic contractor who may
  take any other work package, skill-agnostic.
* A contractor profile stands for as many people as needed: no capacity
  limit. Each work package has at most one contractor candidate
  (`contractor_for`), so the solver never chooses between equal profiles.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.optimization.model import (
    DEFAULT_HOURLY_RATE_CENTS,
    Employee,
    PlanningInput,
    Resource,
    Task,
    qualified,
)

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


def _team(inp: PlanningInput) -> list[Resource]:
    rate_default = inp.constraints.contractor_rate_cents
    team = []
    for employee in inp.employees:
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
    return Resource(
        id=resource_id,
        name=name,
        kind="contractor",
        weekly_capacity_hours=CONTRACTOR_WEEKLY_HOURS,
        rate_cents=rate,
        skills=skills,
        overtime_rate_cents=rate,
    )


def build_resources(inp: PlanningInput) -> tuple[Resource, ...]:
    """The team, then the specialist profiles for skill gaps, then the generic contractor.

    Contractor profiles exist only with `allow_contractors` and a contractor
    rate; the generic one also needs `contractor_any_work`.
    """
    constraints = inp.constraints
    team = _team(inp)
    rate = constraints.contractor_rate_cents
    if not constraints.allow_contractors or rate is None:
        return tuple(team)
    work = [task for task in inp.tasks if not task.done]
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
