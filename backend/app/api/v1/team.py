"""Team endpoints: who is on the project, their hours, type and rate, and the skills it needs.

Purpose: Let the PM staff the project from the organization (reusing an employee by name) and keep
the skill list; rule D1: employees are costed at their company rate, contractors need their own rate
(the project's confirmed contractor rate being the default).
How it fits: Uses app/repositories/team.py (with its skill queries); layer: api.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Response, status

from app.api.deps import EmployeeId, ProjectId, SkillId, Team
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.domain.planning import planning_weeks
from app.domain.team import (
    CONTRACTOR_RATE_DETAIL,
    EMPLOYEE_RATE_DETAIL,
    ProjectSkill,
    ProjectSkillCreate,
    ProjectSkillUpdate,
    TeamMember,
    TeamMemberCreate,
    TeamMemberUpdate,
)
from app.repositories.access import ProjectRef

router = APIRouter(tags=["team"])


async def _project(team: Team, project_id: uuid.UUID) -> ProjectRef:
    """Return the project; 404 when it is hidden."""
    project = await team.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def _editable_project(team: Team, project_id: uuid.UUID) -> ProjectRef:
    """Return the project; 404 when hidden, 403 when the caller can't edit its team."""
    project = await _project(team, project_id)
    if not await team.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this project's team")
    return project


async def _members(team: Team, project: ProjectRef) -> list[TeamMember]:
    """Return the team with hours booked elsewhere over the project's planning window."""
    start, end = await team.window(project)
    return await team.members(project, planning_weeks(start, end, datetime.now(UTC).date()))


async def _contractor_rate(team: Team, project_id: uuid.UUID, *rates: int | None) -> int:
    """The first rate given, else the project's confirmed contractor rate (D1).

    Contractors are costed at their own rate, so one is required: 422 without it.
    """
    for rate in (*rates, await team.confirmed_contractor_rate(project_id)):
        if rate is not None:
            return rate
    raise UnprocessableError(CONTRACTOR_RATE_DETAIL)


async def _member(team: Team, project: ProjectRef, employee_id: uuid.UUID) -> TeamMember:
    """Return one team member; 404 when they are not on the team."""
    found = next(
        (member for member in await _members(team, project) if member.employee_id == employee_id),
        None,
    )
    if found is None:
        raise NotFoundError("Team member not found")
    return found


# --------------------------------------------------------------------------- team
@router.get(
    "/projects/{projectId}/team",
    response_model=list[TeamMember],
    summary="The team on this project, with hours already booked elsewhere",
)
async def list_team(project_id: ProjectId, team: Team) -> list[TeamMember]:
    """Return the project's team with the hours each person is already booked elsewhere."""
    return await _members(team, await _project(team, project_id))


@router.post(
    "/projects/{projectId}/team",
    response_model=TeamMember,
    status_code=status.HTTP_201_CREATED,
    summary="Add a person to the team (reuses the organization's employee with that name)",
)
async def add_team_member(project_id: ProjectId, body: TeamMemberCreate, team: Team) -> TeamMember:
    """Put a person on the team (reusing the organization's employee of that name); return them.

    Returns 409 when they are already on it and 422 when an employee is given a rate or a contractor
    has none (and the project has no confirmed contractor rate).
    """
    project = await _editable_project(team, project_id)
    current = await team.member_names(project_id)
    if body.full_name.casefold() in {name.casefold() for name in current.values()}:
        raise ConflictError(f"{body.full_name} is already on the team")
    contractor = body.worker_type == "contractor"
    if not contractor and body.hourly_rate_cents is not None:
        raise UnprocessableError(EMPLOYEE_RATE_DETAIL)
    employee_id = await team.find_employee(project.org_id, body.full_name)
    if employee_id in current:
        raise ConflictError(f"{body.full_name} is already on the team")
    known = await team.employee_rate(employee_id) if employee_id is not None else None
    rate = (
        await _contractor_rate(team, project_id, body.hourly_rate_cents, known)
        if contractor
        else None
    )
    if employee_id is None:
        employee_id = await team.create_employee(
            project.org_id,
            body.full_name,
            body.role_title,
            body.hours_per_week,
            is_contractor=contractor,
            hourly_rate_cents=rate,
        )
    else:
        # Reusing the organization's record: it takes the type given here; an
        # employee keeps the rate on record.
        await team.set_worker_type(employee_id, contractor)
        if rate is not None:
            await team.set_hourly_rate(employee_id, rate)
    await team.add_member(project_id, employee_id, body.role_title, body.hours_per_week)
    await team.add_employee_skills(
        employee_id, await team.ensure_skills(project.org_id, body.skills)
    )
    return await _member(team, project, employee_id)


@router.patch(
    "/projects/{projectId}/team/{employeeId}",
    response_model=TeamMember,
    summary="Change a team member's role, hours on this project, type, rate, skills or name",
)
async def update_team_member(
    project_id: ProjectId, employee_id: EmployeeId, body: TeamMemberUpdate, team: Team
) -> TeamMember:
    """Change a member's role, hours, type, rate, skills or name and return them.

    Returns 404 when they are not on the team and 409 when the new name is taken on it.
    """
    project = await _editable_project(team, project_id)
    current = await team.member_names(project_id)
    if employee_id not in current:
        raise NotFoundError("Team member not found")
    if body.full_name is not None and body.full_name != current[employee_id]:
        if any(
            other != employee_id and name.casefold() == body.full_name.casefold()
            for other, name in current.items()
        ):
            raise ConflictError(f"{body.full_name} is already on the team")
        await team.rename_employee(employee_id, body.full_name)
    await _update_pay(team, project, employee_id, body)
    changes = body.member_changes()
    if changes:
        await team.update_member(project_id, employee_id, changes)
    if body.skills is not None:
        await team.set_employee_skills(
            employee_id, await team.ensure_skills(project.org_id, body.skills)
        )
    return await _member(team, project, employee_id)


async def _update_pay(
    team: Team, project: ProjectRef, employee_id: uuid.UUID, body: TeamMemberUpdate
) -> None:
    """Apply `workerType` and `hourlyRateCents` with the D1 rules (422 when they break)."""
    if body.worker_type is None and not body.rate_changed:
        return
    member = await _member(team, project, employee_id)
    worker_type = body.worker_type or member.worker_type
    if worker_type == "employee":
        if body.hourly_rate_cents is not None:
            raise UnprocessableError(EMPLOYEE_RATE_DETAIL)
    else:
        given = body.hourly_rate_cents if body.rate_changed else member.hourly_rate_cents
        rate = await _contractor_rate(team, project.id, given)
        if rate != member.hourly_rate_cents:
            await team.set_hourly_rate(employee_id, rate)
    if worker_type != member.worker_type:
        await team.set_worker_type(employee_id, worker_type == "contractor")


@router.delete(
    "/projects/{projectId}/team/{employeeId}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Take a person off this project's team (they stay in the organization; the "
    "work packages they own lose their owner)",
)
async def remove_team_member(
    project_id: ProjectId, employee_id: EmployeeId, team: Team
) -> Response:
    """Take a person off the team (they stay in the organization; their packages lose the owner)."""
    await _editable_project(team, project_id)
    if not await team.remove_member(project_id, employee_id):
        raise NotFoundError("Team member not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- skills
async def _skill(team: Team, project_id: uuid.UUID, skill_id: uuid.UUID) -> ProjectSkill:
    """Return one skill of the project's list; 404 when it is not on it."""
    found = next(
        (skill for skill in await team.skills(project_id) if skill.skill_id == skill_id), None
    )
    if found is None:
        raise NotFoundError("Skill not found in this project's list")
    return found


@router.get(
    "/projects/{projectId}/skills",
    response_model=list[ProjectSkill],
    summary="Skills this project needs and who on the team has them",
)
async def list_skills(project_id: ProjectId, team: Team) -> list[ProjectSkill]:
    """Return the skills the project needs and who on the team holds each."""
    await _project(team, project_id)
    return await team.skills(project_id)


@router.post(
    "/projects/{projectId}/skills",
    response_model=ProjectSkill,
    status_code=status.HTTP_201_CREATED,
    summary="Add a skill to the project's list",
)
async def add_skill(project_id: ProjectId, body: ProjectSkillCreate, team: Team) -> ProjectSkill:
    """Add a skill to the project's list and return it; 409 when it is already listed."""
    project = await _editable_project(team, project_id)
    await team.materialize_skills(project_id)
    if any(
        skill.name.casefold() == body.name.casefold() for skill in await team.skills(project_id)
    ):
        raise ConflictError(f"{body.name} is already in the list")
    ids = await team.ensure_skills(project.org_id, [body.name])
    if not ids:
        raise ForbiddenError("You do not have permission to add skills to this organization")
    skill_id = ids[0]
    await team.add_project_skill(project_id, skill_id, body.required)
    return await _skill(team, project_id, skill_id)


@router.patch(
    "/projects/{projectId}/skills/{skillId}",
    response_model=ProjectSkill,
    summary="Rename a skill or switch it between Required and Optional",
)
async def update_skill(
    project_id: ProjectId, skill_id: SkillId, body: ProjectSkillUpdate, team: Team
) -> ProjectSkill:
    """Rename a skill or switch it between required and optional, and return it.

    Renaming to another existing organization skill moves the project (and its holders) to that one.
    """
    project = await _editable_project(team, project_id)
    await team.materialize_skills(project_id)
    listed = await team.skills(project_id)
    current = next((skill for skill in listed if skill.skill_id == skill_id), None)
    if current is None:
        raise NotFoundError("Skill not found in this project's list")
    required = current.required if body.required is None else body.required
    if body.name is not None and body.name != current.name:
        if any(
            skill.skill_id != skill_id and skill.name.casefold() == body.name.casefold()
            for skill in listed
        ):
            raise ConflictError(f"{body.name} is already in the list")
        existing = await team.org_skill(project.org_id, body.name)
        if existing is not None and existing != skill_id:
            await team.replace_skill(project_id, skill_id, existing, required)
            return await _skill(team, project_id, existing)
        await team.rename_skill(skill_id, body.name)
    if required != current.required:
        await team.set_required(project_id, skill_id, required)
    return await _skill(team, project_id, skill_id)


@router.delete(
    "/projects/{projectId}/skills/{skillId}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a skill from the project's list",
)
async def remove_skill(project_id: ProjectId, skill_id: SkillId, team: Team) -> Response:
    """Remove a skill from the project's list; 404 when it is not on it."""
    await _editable_project(team, project_id)
    await team.materialize_skills(project_id)
    if not await team.remove_skill(project_id, skill_id):
        raise NotFoundError("Skill not found in this project's list")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
