from __future__ import annotations

import uuid
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
    cycle_path,
    planning_weeks,
    skill_coverage,
)
from app.repositories.access import ProjectRef

router = APIRouter(tags=["planning"])

NODE_NOT_IN_PROJECT = "Work package not found in this project"


async def _project(planning: Planning, project_id: ProjectId) -> ProjectRef:
    project = await planning.project(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def _writable_project(planning: Planning, project_id: ProjectId) -> ProjectRef:
    project = await _project(planning, project_id)
    if not await planning.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this project's plan")
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
async def _ensure_project_nodes(
    planning: Planning, project_id: uuid.UUID, node_ids: set[uuid.UUID]
) -> None:
    """404 unless every node is a WBS node of the project that the caller can see.

    Security rule (SEC-001): a dependency links work packages of its own project only.
    Unknown ids and other projects' ids get the same 404, so the answer tells nothing
    about other workspaces (the database enforces the same rule)."""
    if node_ids - await planning.node_ids(project_id, node_ids):
        raise NotFoundError(NODE_NOT_IN_PROJECT)


async def _ensure_no_cycle(
    planning: Planning, project_id: uuid.UUID, body: DependencyCreate
) -> None:
    """422 naming the cycle the new link would close ("1.1 A → 1.2 B → 1.1 A", WBS-009).

    The database refuses cycles too (without naming them), for links added concurrently."""
    existing = await planning.dependencies(project_id)
    links = [(link.predecessor_id, link.successor_id) for link in existing]
    cycle = cycle_path(links, body.predecessor_id, body.successor_id)
    if cycle is None:
        return
    labels = {link.predecessor_id: link.predecessor_label for link in existing}
    labels |= {link.successor_id: link.successor_label for link in existing}
    named = " → ".join(labels.get(node_id) or str(node_id) for node_id in cycle)
    raise UnprocessableError(f"Dependency would create a cycle: {named}")


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
    """Link two work packages of the project (404 when one isn't the project's)."""
    await _writable_project(planning, project_id)
    await _ensure_project_nodes(planning, project_id, {body.predecessor_id, body.successor_id})
    await _ensure_no_cycle(planning, project_id, body)
    dependency_id = await planning.create_dependency(project_id, body)
    dependency = await planning.dependency(dependency_id)
    if dependency is None:
        raise ForbiddenError("The dependency was created but is not visible to you")
    return dependency


async def _writable_dependency(planning: Planning, dependency_id: DependencyId) -> uuid.UUID:
    project_id = await planning.dependency_project(dependency_id)
    if project_id is None:
        raise NotFoundError("Dependency not found")
    if not await planning.can_write(project_id):
        raise ForbiddenError("You do not have permission to edit this project's plan")
    return project_id


@router.patch(
    "/dependencies/{dependencyId}", response_model=Dependency, summary="Update a dependency"
)
async def update_dependency(
    dependency_id: DependencyId, body: DependencyUpdate, planning: Planning
) -> Dependency:
    """Change a dependency's type or lag (404 when it links another project's work)."""
    project_id = await _writable_dependency(planning, dependency_id)
    current = await planning.dependency(dependency_id)
    if current is None:
        raise NotFoundError("Dependency not found")
    await _ensure_project_nodes(
        planning, project_id, {current.predecessor_id, current.successor_id}
    )
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
