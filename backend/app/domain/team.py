"""API models of the team on a project and the skills it needs.

Purpose: Every team member is an employee (already in the company) or a contractor
(`employees.is_contractor`, round-2 decision D1). Employees are costed at the rate on their company
record, never one typed here; contractors need an hourly rate.
How it fits: Used by app/api/v1/team.py and app/repositories/team.py; app/domain/changes.py reuses
the field types for people who join mid-project; layer: domain.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.domain.common import CamelModel, InputModel

PersonName = Annotated[str, StringConstraints(min_length=1, max_length=200)]
RoleTitle = Annotated[str, StringConstraints(min_length=1, max_length=120)]
SkillName = Annotated[str, StringConstraints(min_length=1, max_length=100)]
HoursPerWeek = Annotated[int, Field(ge=1, le=80)]
# Cents per hour; up to $1M/h so a typo can't overflow the labor cost.
HourlyRateCents = Annotated[int, Field(ge=0, le=100_000_000)]
SkillNames = Annotated[list[SkillName], Field(max_length=50)]
WorkerType = Literal["employee", "contractor"]

EMPLOYEE_RATE_DETAIL = "Employee rates come from the company record."
CONTRACTOR_RATE_DETAIL = "Enter the contractor's hourly rate."


def clean_skill_names(names: list[str]) -> list[str]:
    """Trimmed names without blanks or case-insensitive duplicates (first spelling wins)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = " ".join(raw.split())
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


# --------------------------------------------------------------------------- team
class TeamMember(CamelModel):
    """A person on the project with hours, rate, booked hours and skills."""

    employee_id: uuid.UUID
    full_name: str
    role_title: str | None
    hours_per_week: float
    worker_type: WorkerType
    # Employee: the company record's rate (null = the standard rate);
    # contractor: their contracted rate.
    hourly_rate_cents: int | None
    booked_hours: float
    skills: list[str]


class TeamMemberCreate(InputModel):
    """A person to add. Only contractors take a rate (stored on the organization record)."""

    full_name: PersonName
    role_title: RoleTitle
    hours_per_week: HoursPerWeek
    worker_type: WorkerType = "employee"
    hourly_rate_cents: HourlyRateCents | None = None
    skills: SkillNames = Field(default_factory=list)

    @field_validator("full_name")
    @classmethod
    def _squash(cls, value: str) -> str:
        """Collapse runs of whitespace in the name."""
        return " ".join(value.split())

    @field_validator("skills")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        """Trim the skill names and drop blanks and duplicates."""
        return clean_skill_names(value)


class TeamMemberUpdate(InputModel):
    """Partial update; `fullName`, `workerType` and a contractor's `hourlyRateCents`
    change the organization's record. A contractor's rate can't be cleared."""

    full_name: PersonName | None = None
    role_title: RoleTitle | None = None
    hours_per_week: HoursPerWeek | None = None
    worker_type: WorkerType | None = None
    hourly_rate_cents: HourlyRateCents | None = None
    skills: SkillNames | None = None

    @model_validator(mode="after")
    def _validate(self) -> TeamMemberUpdate:
        """Reject an empty body and nulls (except the rate), then normalise the name and skills."""
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        nulls = sorted(
            name
            for name in self.model_fields_set - {"hourly_rate_cents"}
            if getattr(self, name) is None
        )
        if nulls:
            raise ValueError(f"Fields cannot be null: {', '.join(nulls)}")
        if self.full_name is not None:
            self.full_name = " ".join(self.full_name.split())
        if self.skills is not None:
            self.skills = clean_skill_names(self.skills)
        return self

    def member_changes(self) -> dict[str, Any]:
        """Columns of project_team_members to update."""
        out: dict[str, Any] = {}
        if self.role_title is not None:
            out["role_title"] = self.role_title
        if self.hours_per_week is not None:
            out["hours_per_week"] = self.hours_per_week
        return out

    @property
    def rate_changed(self) -> bool:
        """Whether the body sends a rate (null included)."""
        return "hourly_rate_cents" in self.model_fields_set


# --------------------------------------------------------------------------- skills
class ProjectSkill(CamelModel):
    """A skill the project needs and which team members hold it."""

    skill_id: uuid.UUID
    name: str
    required: bool
    holders: list[str]


class ProjectSkillCreate(InputModel):
    """Body to add a needed skill."""

    name: SkillName
    required: bool = True

    @field_validator("name")
    @classmethod
    def _squash(cls, value: str) -> str:
        """Collapse runs of whitespace in the skill name."""
        return " ".join(value.split())


class ProjectSkillUpdate(InputModel):
    """Body to rename a needed skill or change whether it is required."""

    name: SkillName | None = None
    required: bool | None = None

    @model_validator(mode="after")
    def _validate(self) -> ProjectSkillUpdate:
        """Reject an empty body and nulls, then normalise the name."""
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        nulls = sorted(
            field_name for field_name in self.model_fields_set if getattr(self, field_name) is None
        )
        if nulls:
            raise ValueError(f"Fields cannot be null: {', '.join(nulls)}")
        if self.name is not None:
            self.name = " ".join(self.name.split())
        return self
