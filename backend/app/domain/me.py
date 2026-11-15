"""API models of the signed-in user: profile, organization memberships and profile edits.

Purpose: Let the app show who is signed in, which organizations they belong to (with which role) and
let them edit their own name and job title.
How it fits: Used by app/api/v1/me.py and app/repositories/me.py; layer: domain.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

from pydantic import Field, model_validator

from app.domain.common import CamelModel, InputModel, OrgRole


class OrganizationMembership(CamelModel):
    """An organization the user belongs to, with their role in it."""

    id: uuid.UUID
    name: str
    slug: str
    role: OrgRole


class Profile(CamelModel):
    """The user's profile row."""

    id: uuid.UUID
    email: str
    full_name: str | None
    avatar_url: str | None
    job_title: str | None


class Me(Profile):
    """The signed-in user's profile plus their organization memberships."""

    organizations: list[OrganizationMembership]


class MeUpdate(InputModel):
    """Body of `PATCH /me`: only fields present are changed."""

    full_name: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _not_empty(self) -> MeUpdate:
        """Reject an empty body."""
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self

    def changes(self) -> dict[str, str | None]:
        """Return the columns to update; empty strings clear the field (stored as null)."""
        # Empty strings clear the field.
        return {
            field_name: (value or None)
            for field_name, value in self.model_dump(exclude_unset=True, by_alias=False).items()
        }
