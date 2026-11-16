"""FastAPI dependency providers: one repository or job runner per request.

Purpose: Route handlers ask for what they need by type (`Projects`, `Wbs`, `Optimizer`, ...) and get
it bound to the caller's RLS-scoped connection; tests override these providers with fakes.
How it fits: Imported by every module in app/api/v1; builds app/repositories classes over
`app.core.db.UserDB` and reads the job runners from `app.state`; layer: api.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

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
from app.repositories.ai_usage import ServiceConnections
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
    """Provide the profile repository on the caller's connection."""
    return ProfileRepository(db)


def get_org_repo(db: UserDB) -> OrgRepository:
    """Provide the organization repository on the caller's connection."""
    return OrgRepository(db)


def get_project_repo(db: UserDB) -> ProjectRepository:
    """Provide the project repository on the caller's connection."""
    return ProjectRepository(db)


def get_notification_repo(db: UserDB) -> NotificationRepository:
    """Provide the notification repository on the caller's connection."""
    return NotificationRepository(db)


def get_optimization_repo(db: UserDB) -> OptimizationRepository:
    """Provide the optimization run repository on the caller's connection."""
    return OptimizationRepository(db)


def get_optimization_runner(request: Request) -> OptimizationRunner:
    """Provide the in-process optimizer; 503 when it is not available."""
    runner: OptimizationRunner | None = getattr(request.app.state, "optimizer", None)
    if runner is None:
        raise ServiceUnavailableError("The optimizer is not available")
    return runner


def get_intake_repo(db: UserDB) -> IntakeRepository:
    """Provide the AI intake repository on the caller's connection."""
    return IntakeRepository(db)


def get_wbs_repo(db: UserDB) -> WbsRepository:
    """Provide the WBS repository on the caller's connection."""
    return WbsRepository(db)


def get_planning_repo(db: UserDB) -> PlanningRepository:
    """Provide the planning (constraints, resources, dependencies) repository."""
    return PlanningRepository(db)


def get_plan_repo(db: UserDB) -> PlanRepository:
    """Provide the plan versions and export repository."""
    return PlanRepository(db)


def get_overview_repo(db: UserDB) -> OverviewRepository:
    """Provide the project overview repository."""
    return OverviewRepository(db)


def get_team_repo(db: UserDB) -> TeamRepository:
    """Provide the project team and skills repository."""
    return TeamRepository(db)


def get_intake_runner(request: Request) -> IntakeRunner:
    """Provide the in-process AI intake runner; 503 when it is not available."""
    intaker: IntakeRunner | None = getattr(request.app.state, "intaker", None)
    if intaker is None:
        raise ServiceUnavailableError("AI intake is not available")
    return intaker


def get_ai_job_repo(db: UserDB) -> AiJobRepository:
    """Provide the AI job repository on the caller's connection."""
    return AiJobRepository(db)


def get_ai_runner(request: Request) -> AiJobRunner:
    """Provide the in-process AI job runner; 503 when it is not available."""
    runner: AiJobRunner | None = getattr(request.app.state, "ai_runner", None)
    if runner is None:
        raise ServiceUnavailableError("AI jobs are not available")
    return runner


def get_assistant_repo(db: UserDB) -> AssistantRepository:
    """Provide the assistant's read-only project context repository."""
    return AssistantRepository(db)


def get_assistant_llm(request: Request) -> LLM:
    """Provide the Claude client the assistant uses."""
    llm: LLM = request.app.state.assistant_llm
    return llm


def get_ai_usage_connections(request: Request) -> ServiceConnections | None:
    """Provide where AI features record their token use: one service-connection transaction per
    row, apart from the caller's own (None without a database: nothing is recorded)."""
    engine = getattr(request.app.state, "engine", None)
    return engine.begin if engine is not None else None


def get_document_repo(db: UserDB) -> DocumentRepository:
    """Provide the uploaded documents repository on the caller's connection."""
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
AiUsageLog = Annotated[ServiceConnections | None, Depends(get_ai_usage_connections)]
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
