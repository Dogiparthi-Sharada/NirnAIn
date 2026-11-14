"""Repository providers. Tests override these to swap in fakes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path, Request

from app.ai.llm import LLM
from app.core.db import UserDB
from app.core.errors import ServiceUnavailableError
from app.jobs.ai_jobs import AiJobRunner
from app.jobs.intake import IntakeRunner
from app.jobs.optimizer import OptimizationRunner
from app.repositories.ai_jobs import AiJobRepository
from app.repositories.assistant import AssistantRepository
from app.repositories.documents import DocumentRepository
from app.repositories.intake import IntakeRepository
from app.repositories.me import ProfileRepository
from app.repositories.notifications import NotificationRepository
from app.repositories.optimization import OptimizationRepository
from app.repositories.orgs import OrgRepository
from app.repositories.overview import OverviewRepository
from app.repositories.plan import PlanRepository
from app.repositories.planning import PlanningRepository
from app.repositories.projects import ProjectRepository
from app.repositories.team import TeamRepository
from app.repositories.wbs import WbsRepository


def get_profile_repo(db: UserDB) -> ProfileRepository:
    return ProfileRepository(db)


def get_org_repo(db: UserDB) -> OrgRepository:
    return OrgRepository(db)


def get_project_repo(db: UserDB) -> ProjectRepository:
    return ProjectRepository(db)


def get_notification_repo(db: UserDB) -> NotificationRepository:
    return NotificationRepository(db)


def get_optimization_repo(db: UserDB) -> OptimizationRepository:
    return OptimizationRepository(db)


def get_optimization_runner(request: Request) -> OptimizationRunner:
    runner: OptimizationRunner | None = getattr(request.app.state, "optimizer", None)
    if runner is None:
        raise ServiceUnavailableError("The optimizer is not available")
    return runner


def get_intake_repo(db: UserDB) -> IntakeRepository:
    return IntakeRepository(db)


def get_wbs_repo(db: UserDB) -> WbsRepository:
    return WbsRepository(db)


def get_planning_repo(db: UserDB) -> PlanningRepository:
    return PlanningRepository(db)


def get_plan_repo(db: UserDB) -> PlanRepository:
    return PlanRepository(db)


def get_overview_repo(db: UserDB) -> OverviewRepository:
    return OverviewRepository(db)


def get_team_repo(db: UserDB) -> TeamRepository:
    return TeamRepository(db)


def get_intake_runner(request: Request) -> IntakeRunner:
    intaker: IntakeRunner | None = getattr(request.app.state, "intaker", None)
    if intaker is None:
        raise ServiceUnavailableError("AI intake is not available")
    return intaker


def get_ai_job_repo(db: UserDB) -> AiJobRepository:
    return AiJobRepository(db)


def get_ai_runner(request: Request) -> AiJobRunner:
    runner: AiJobRunner | None = getattr(request.app.state, "ai_runner", None)
    if runner is None:
        raise ServiceUnavailableError("AI jobs are not available")
    return runner


def get_assistant_repo(db: UserDB) -> AssistantRepository:
    return AssistantRepository(db)


def get_assistant_llm(request: Request) -> LLM:
    llm: LLM = request.app.state.assistant_llm
    return llm


def get_document_repo(db: UserDB) -> DocumentRepository:
    """Uploaded intake documents, under the caller's RLS session."""
    return DocumentRepository(db)


Profiles = Annotated[ProfileRepository, Depends(get_profile_repo)]
Orgs = Annotated[OrgRepository, Depends(get_org_repo)]
Projects = Annotated[ProjectRepository, Depends(get_project_repo)]
Notifications = Annotated[NotificationRepository, Depends(get_notification_repo)]
OptimizationRuns = Annotated[OptimizationRepository, Depends(get_optimization_repo)]
Optimizer = Annotated[OptimizationRunner, Depends(get_optimization_runner)]
Intakes = Annotated[IntakeRepository, Depends(get_intake_repo)]
Intaker = Annotated[IntakeRunner, Depends(get_intake_runner)]
Wbs = Annotated[WbsRepository, Depends(get_wbs_repo)]
Planning = Annotated[PlanningRepository, Depends(get_planning_repo)]
Plans = Annotated[PlanRepository, Depends(get_plan_repo)]
Overviews = Annotated[OverviewRepository, Depends(get_overview_repo)]
AiJobs = Annotated[AiJobRepository, Depends(get_ai_job_repo)]
AiRunner = Annotated[AiJobRunner, Depends(get_ai_runner)]
AssistantContexts = Annotated[AssistantRepository, Depends(get_assistant_repo)]
AssistantLLM = Annotated[LLM, Depends(get_assistant_llm)]
Documents = Annotated[DocumentRepository, Depends(get_document_repo)]
Team = Annotated[TeamRepository, Depends(get_team_repo)]

# Path parameters are camelCase on the wire, like everything else.
OrgId = Annotated[uuid.UUID, Path(alias="orgId")]
ProjectId = Annotated[uuid.UUID, Path(alias="projectId")]
NotificationId = Annotated[uuid.UUID, Path(alias="notificationId")]
RunId = Annotated[uuid.UUID, Path(alias="runId")]
ItemId = Annotated[uuid.UUID, Path(alias="itemId")]
NodeId = Annotated[uuid.UUID, Path(alias="nodeId")]
DependencyId = Annotated[uuid.UUID, Path(alias="dependencyId")]
DocumentId = Annotated[uuid.UUID, Path(alias="documentId")]
EmployeeId = Annotated[uuid.UUID, Path(alias="employeeId")]
SkillId = Annotated[uuid.UUID, Path(alias="skillId")]
