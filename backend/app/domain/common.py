"""Shared schema primitives and mirrors of the Postgres enum types.

Purpose: Keep the wire format consistent (camelCase JSON, strict request bodies) and the enum values
identical to the `public.*` enum types in `supabase/migrations`, the source of truth.
How it fits: Every other domain module builds on `CamelModel`/`InputModel`; repositories map the
enums onto table columns; layer: domain.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base schema: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        from_attributes=True,
    )


class InputModel(CamelModel):
    """Request bodies: reject unknown fields and trim strings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OrgRole(StrEnum):
    """Mirror of `public.org_role`: a member's role in an organization."""

    OWNER = "owner"
    ADMIN = "admin"
    PM = "pm"
    MEMBER = "member"
    VIEWER = "viewer"


class ProjectType(StrEnum):
    """Mirror of `public.project_type`: the kind of project."""

    TECHNOLOGY = "technology"
    CONSTRUCTION = "construction"
    PRODUCT_DEVELOPMENT = "product_development"
    BUSINESS_PROCESS = "business_process"
    COMPLIANCE = "compliance"


class ProjectPriority(StrEnum):
    """Mirror of `public.project_priority`."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ProjectMethodology(StrEnum):
    """Mirror of `public.project_methodology`: how the project is run."""

    WATERFALL = "waterfall"
    AGILE = "agile"
    HYBRID = "hybrid"
    KANBAN = "kanban"


class ProjectVisibility(StrEnum):
    """Mirror of `public.project_visibility`: who may see the project."""

    INTERNAL = "internal"
    CLIENT_FACING = "client_facing"
    CONFIDENTIAL = "confidential"


class ProjectStatus(StrEnum):
    """Mirror of `public.project_status`: where the project stands."""

    DRAFT = "draft"
    PLANNING = "planning"
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    DELAYED = "delayed"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ProjectStep(StrEnum):
    """Mirror of `public.project_step`: the workflow step the project has reached."""

    CREATE = "create"
    INTAKE = "intake"
    SCOPE = "scope"
    WBS = "wbs"
    CONSTRAINTS = "constraints"
    OPTIMIZE = "optimize"
    DONE = "done"


class NotificationLevel(StrEnum):
    """Mirror of `public.notification_level`."""

    INFO = "info"
    WARN = "warn"
    ALERT = "alert"


class SolverStatus(StrEnum):
    """Mirror of `public.solver_status`: the state of an optimization run."""

    QUEUED = "queued"
    RUNNING = "running"
    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    TIMEOUT = "timeout"
    FAILED = "failed"


class DependencyType(StrEnum):
    """Mirror of `public.dependency_type`: finish-to-start, start-to-start, and so on."""

    FS = "FS"
    SS = "SS"
    FF = "FF"
    SF = "SF"


class ConstraintMode(StrEnum):
    """Mirror of `public.constraint_mode`: hard (must hold) or soft (can flex)."""

    HARD = "hard"
    SOFT = "soft"


class RecommendationType(StrEnum):
    """Mirror of `public.recommendation_type`: kinds of optimizer recommendations."""

    REASSIGNMENT = "reassignment"
    SCHEDULE_CHANGE = "schedule_change"
    HIRE = "hire"
    NOTE = "note"


class DocumentKind(StrEnum):
    """Mirror of `public.document_kind`: the type of an uploaded source document."""

    TEXT = "text"
    PDF = "pdf"
    DOCX = "docx"
    TRANSCRIPT = "transcript"
    AUDIO = "audio"


class JobStatus(StrEnum):
    """Mirror of `public.job_status`: the state of a background job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScopeCategory(StrEnum):
    """Mirror of `public.scope_category`: what a scope item is."""

    OBJECTIVE = "objective"
    REQUIREMENT = "requirement"
    DELIVERABLE = "deliverable"
    CONSTRAINT = "constraint"
    RISK = "risk"
    MISSING_INFO = "missing_info"


class ScopeStatus(StrEnum):
    """Mirror of `public.scope_status`: the PM's review decision on a scope item."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REMOVED = "removed"


class ItemOrigin(StrEnum):
    """Mirror of `public.item_origin`: whether AI or a person created the item."""

    AI = "ai"
    MANUAL = "manual"


class Evidence(StrEnum):
    """Allowed `scope_items.evidence` values ("never invent": how well the quote backs it)."""

    STATED = "stated"  # the source quote says it
    INFERRED = "inferred"  # derived from the quote, or no exact quote was found
    FLAGGED = "flagged"  # says more than the quote; suggested_text is what the quote supports


class AiJobKind(StrEnum):
    """Mirror of `public.ai_job_kind`: the kinds of background AI jobs."""

    WBS_GENERATE = "wbs_generate"
    WBS_VALIDATE = "wbs_validate"


class Page[ItemT](CamelModel):
    """One page of a keyset-paginated list and the cursor of the next page."""

    items: list[ItemT]
    next_cursor: str | None = None
