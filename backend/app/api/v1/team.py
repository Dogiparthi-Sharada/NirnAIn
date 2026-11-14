from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Response, status

from app.api.deps import EmployeeId, ProjectId, SkillId, Team
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.domain.planning import planning_weeks
from app.domain.team import (
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
    project = await team.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def _editable_project(team: Team, project_id: uuid.UUID) -> ProjectRef:
    project = await _project(team, project_id)
    if not await team.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this project's team")
    return project


async def _members(team: Team, project: ProjectRef) -> list[TeamMember]:
    start, end = await team.window(project)
    return await team.members(project, planning_weeks(start, end, datetime.now(UTC).date()))


async def _member(team: Team, project: ProjectRef, employee_id: uuid.UUID) -> TeamMember:
    found = next((m for m in await _members(team, project) if m.employee_id == employee_id), None)
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
    return await _members(team, await _project(team, project_id))


@router.post(
    "/projects/{projectId}/team",
    response_model=TeamMember,
    status_code=status.HTTP_201_CREATED,
    summary="Add a person to the team (reuses the organization's employee with that name)",
)
async def add_team_member(project_id: ProjectId, body: TeamMemberCreate, team: Team) -> TeamMember:
    project = await _editable_project(team, project_id)
    current = await team.member_names(project_id)
    if body.full_name.casefold() in {name.casefold() for name in current.values()}:
        raise ConflictError(f"{body.full_name} is already on the team")
    employee_id = await team.find_employee(project.org_id, body.full_name)
    if employee_id is None:
        employee_id = await team.create_employee(
            project.org_id,
            body.full_name,
            body.role_title,
            body.hours_per_week,
            hourly_rate_cents=body.hourly_rate_cents,
        )
    elif employee_id in current:
        raise ConflictError(f"{body.full_name} is already on the team")
    elif body.hourly_rate_cents is not None:
        await team.set_hourly_rate(employee_id, body.hourly_rate_cents)
    await team.add_member(project_id, employee_id, body.role_title, body.hours_per_week)
    await team.add_employee_skills(
        employee_id, await team.ensure_skills(project.org_id, body.skills)
    )
    return await _member(team, project, employee_id)


@router.patch(
    "/projects/{projectId}/team/{employeeId}",
    response_model=TeamMember,
    summary="Change a team member's role, hours on this project, rate, skills or name",
)
async def update_team_member(
    project_id: ProjectId, employee_id: EmployeeId, body: TeamMemberUpdate, team: Team
) -> TeamMember:
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
    if body.rate_changed:
        await team.set_hourly_rate(employee_id, body.hourly_rate_cents)
    changes = body.member_changes()
    if changes:
        await team.update_member(project_id, employee_id, changes)
    if body.skills is not None:
        await team.set_employee_skills(
            employee_id, await team.ensure_skills(project.org_id, body.skills)
        )
    return await _member(team, project, employee_id)


@router.delete(
    "/projects/{projectId}/team/{employeeId}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Take a person off this project's team (they stay in the organization)",
)
async def remove_team_member(
    project_id: ProjectId, employee_id: EmployeeId, team: Team
) -> Response:
    await _editable_project(team, project_id)
    if not await team.remove_member(project_id, employee_id):
        raise NotFoundError("Team member not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- skills
async def _skill(team: Team, project_id: uuid.UUID, skill_id: uuid.UUID) -> ProjectSkill:
    found = next((s for s in await team.skills(project_id) if s.skill_id == skill_id), None)
    if found is None:
        raise NotFoundError("Skill not found in this project's list")
    return found


@router.get(
    "/projects/{projectId}/skills",
    response_model=list[ProjectSkill],
    summary="Skills this project needs and who on the team has them",
)
async def list_skills(project_id: ProjectId, team: Team) -> list[ProjectSkill]:
    await _project(team, project_id)
    return await team.skills(project_id)


@router.post(
    "/projects/{projectId}/skills",
    response_model=ProjectSkill,
    status_code=status.HTTP_201_CREATED,
    summary="Add a skill to the project's list",
)
async def add_skill(project_id: ProjectId, body: ProjectSkillCreate, team: Team) -> ProjectSkill:
    project = await _editable_project(team, project_id)
    await team.materialize_skills(project_id)
    if any(s.name.casefold() == body.name.casefold() for s in await team.skills(project_id)):
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
    project = await _editable_project(team, project_id)
    await team.materialize_skills(project_id)
    listed = await team.skills(project_id)
    current = next((s for s in listed if s.skill_id == skill_id), None)
    if current is None:
        raise NotFoundError("Skill not found in this project's list")
    required = current.required if body.required is None else body.required
    if body.name is not None and body.name != current.name:
        if any(
            s.skill_id != skill_id and s.name.casefold() == body.name.casefold() for s in listed
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
    await _editable_project(team, project_id)
    await team.materialize_skills(project_id)
    if not await team.remove_skill(project_id, skill_id):
        raise NotFoundError("Skill not found in this project's list")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
