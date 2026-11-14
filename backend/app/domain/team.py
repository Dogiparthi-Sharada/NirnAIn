"""The team on a project, the skills it needs, and the plan lock on its inputs."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.core.errors import ConflictError
from app.domain.common import CamelModel, InputModel

PersonName = Annotated[str, StringConstraints(min_length=1, max_length=200)]
RoleTitle = Annotated[str, StringConstraints(min_length=1, max_length=120)]
SkillName = Annotated[str, StringConstraints(min_length=1, max_length=100)]
HoursPerWeek = Annotated[int, Field(ge=1, le=80)]
SkillNames = Annotated[list[SkillName], Field(max_length=50)]


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


def ensure_unlocked(version: int | None) -> None:
    """Inputs are frozen once a plan version is in force; changes go through step 7."""
    if version is not None:
        raise ConflictError(
            f"Plan v{version} is in force; record a change on step 7",
            extra={"version": version},
        )


# --------------------------------------------------------------------------- team
class TeamMember(CamelModel):
    employee_id: uuid.UUID
    full_name: str
    role_title: str | None
    hours_per_week: float
    booked_hours: float
    skills: list[str]


class TeamMemberCreate(InputModel):
    full_name: PersonName
    role_title: RoleTitle
    hours_per_week: HoursPerWeek
    skills: SkillNames = Field(default_factory=list)

    @field_validator("full_name")
    @classmethod
    def _squash(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("skills")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return clean_skill_names(value)


class TeamMemberUpdate(InputModel):
    """Partial update; `fullName` renames the organization's employee record."""

    full_name: PersonName | None = None
    role_title: RoleTitle | None = None
    hours_per_week: HoursPerWeek | None = None
    skills: SkillNames | None = None

    @model_validator(mode="after")
    def _validate(self) -> TeamMemberUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        nulls = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
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


# --------------------------------------------------------------------------- skills
class ProjectSkill(CamelModel):
    skill_id: uuid.UUID
    name: str
    required: bool
    holders: list[str]


class ProjectSkillCreate(InputModel):
    name: SkillName
    required: bool = True

    @field_validator("name")
    @classmethod
    def _squash(cls, value: str) -> str:
        return " ".join(value.split())


class ProjectSkillUpdate(InputModel):
    name: SkillName | None = None
    required: bool | None = None

    @model_validator(mode="after")
    def _validate(self) -> ProjectSkillUpdate:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        nulls = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
        if nulls:
            raise ValueError(f"Fields cannot be null: {', '.join(nulls)}")
        if self.name is not None:
            self.name = " ".join(self.name.split())
        return self
