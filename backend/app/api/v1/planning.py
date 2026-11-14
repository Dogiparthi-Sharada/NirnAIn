from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Response, status

from app.api.deps import DependencyId, OrgId, Orgs, Planning, ProjectId
from app.api.v1.projects import ensure_org_visible
from app.core.errors import ForbiddenError, NotFoundError, UnprocessableError
from app.domain.planning import (
    CapacityEntry,
    ConstraintSet,
    ConstraintSetInput,
    Dependency,
    DependencyCreate,
    DependencyUpdate,
    PoolEmployee,
    SkillCoverage,
    planning_weeks,
    skill_coverage,
)
from app.domain.team import ensure_unlocked
from app.repositories.access import ProjectRef

router = APIRouter(tags=["planning"])


async def _project(planning: Planning, project_id: ProjectId) -> ProjectRef:
    project = await planning.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def _writable_project(planning: Planning, project_id: ProjectId) -> ProjectRef:
    project = await _project(planning, project_id)
    if not await planning.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this project's plan")
    ensure_unlocked(await planning.applied_version(project_id))
    return project


# --------------------------------------------------------------------------- constraints
@router.get(
    "/projects/{projectId}/constraints",
    response_model=ConstraintSet,
    summary="Project constraints (defaults from the project until saved)",
)
async def get_constraints(project_id: ProjectId, planning: Planning) -> ConstraintSet:
    return await planning.constraints(await _project(planning, project_id))


@router.put(
    "/projects/{projectId}/constraints",
    response_model=ConstraintSet,
    summary="Save project constraints and milestones",
)
async def put_constraints(
    project_id: ProjectId, body: ConstraintSetInput, planning: Planning
) -> ConstraintSet:
    project = await _writable_project(planning, project_id)
    nodes = {m.wbs_node_id for m in body.milestones if m.wbs_node_id is not None}
    if nodes - await planning.node_ids(project_id, nodes):
        raise UnprocessableError("Milestone wbsNodeId must be a WBS node of this project")
    await planning.save_constraints(project_id, body)
    return await planning.constraints(project)


# --------------------------------------------------------------------------- resources
@router.get(
    "/projects/{projectId}/capacity",
    response_model=list[CapacityEntry],
    summary="Team capacity with average weekly hours committed to other projects",
)
async def get_capacity(project_id: ProjectId, planning: Planning) -> list[CapacityEntry]:
    project = await _project(planning, project_id)
    cs = await planning.constraints(project)
    weeks = planning_weeks(
        cs.start_date or project.start_date,
        cs.must_finish_by or project.target_end_date,
        datetime.now(UTC).date(),
    )
    return await planning.capacity(project, weeks)


@router.get(
    "/projects/{projectId}/skills-coverage",
    response_model=list[SkillCoverage],
    summary="Required skills and whether the team covers them",
)
async def get_skills_coverage(project_id: ProjectId, planning: Planning) -> list[SkillCoverage]:
    project = await _project(planning, project_id)
    return skill_coverage(
        await planning.required_skills(project_id), await planning.held_skills(project.org_id)
    )


@router.get(
    "/orgs/{orgId}/employees",
    response_model=list[PoolEmployee],
    summary="Active employees of an organization (resource pool)",
)
async def list_employees(org_id: OrgId, orgs: Orgs, planning: Planning) -> list[PoolEmployee]:
    await ensure_org_visible(orgs, org_id)
    return await planning.employees(org_id)


# --------------------------------------------------------------------------- dependencies
@router.get(
    "/projects/{projectId}/dependencies",
    response_model=list[Dependency],
    summary="Task dependencies of a project",
)
async def list_dependencies(project_id: ProjectId, planning: Planning) -> list[Dependency]:
    await _project(planning, project_id)
    return await planning.dependencies(project_id)


@router.post(
    "/projects/{projectId}/dependencies",
    response_model=Dependency,
    status_code=status.HTTP_201_CREATED,
    summary="Add a dependency (cycles and cross-project links are rejected)",
)
async def create_dependency(
    project_id: ProjectId, body: DependencyCreate, planning: Planning
) -> Dependency:
    await _writable_project(planning, project_id)
    dependency_id = await planning.create_dependency(project_id, body)
    dependency = await planning.dependency(dependency_id)
    if dependency is None:
        raise ForbiddenError("The dependency was created but is not visible to you")
    return dependency


async def _writable_dependency(planning: Planning, dependency_id: DependencyId) -> None:
    project_id = await planning.dependency_project(dependency_id)
    if project_id is None:
        raise NotFoundError("Dependency not found")
    if not await planning.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this project's plan")
    ensure_unlocked(await planning.applied_version(project_id))


@router.patch(
    "/dependencies/{dependencyId}", response_model=Dependency, summary="Update a dependency"
)
async def update_dependency(
    dependency_id: DependencyId, body: DependencyUpdate, planning: Planning
) -> Dependency:
    await _writable_dependency(planning, dependency_id)
    if not await planning.update_dependency(dependency_id, body.changes()):
        raise ForbiddenError("You do not have permission to edit this project's plan")
    dependency = await planning.dependency(dependency_id)
    if dependency is None:
        raise NotFoundError("Dependency not found")
    return dependency


@router.delete(
    "/dependencies/{dependencyId}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a dependency",
)
async def delete_dependency(dependency_id: DependencyId, planning: Planning) -> Response:
    await _writable_dependency(planning, dependency_id)
    if not await planning.delete_dependency(dependency_id):
        raise ForbiddenError("You do not have permission to edit this project's plan")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
