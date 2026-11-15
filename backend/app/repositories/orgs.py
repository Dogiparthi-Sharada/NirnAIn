"""Repository of organizations and their memberships.

Purpose: Tell whether the caller belongs to an organization and list its members and the caller's
own memberships; RLS hides organizations the caller is not in.
How it fits: Used by app/api/deps.py (org access checks), app/api/v1/orgs.py and app/api/v1/me.py;
layer: repositories.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.me import OrganizationMembership
from app.domain.orgs import OrgMember
from app.repositories.tables import organization_members, organizations, profiles


class OrgRepository:
    """Organization and membership queries. All run under the caller's RLS context."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def is_visible(self, org_id: uuid.UUID) -> bool:
        """True if the caller is a member (RLS hides other organizations)."""
        stmt = (
            sa.select(sa.literal(1)).select_from(organizations).where(organizations.c.id == org_id)
        )
        return (await self._conn.execute(stmt)).first() is not None

    async def is_member(self, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Tell whether the user is a member of the organization."""
        stmt = sa.select(sa.literal(1)).where(
            organization_members.c.org_id == org_id,
            organization_members.c.user_id == user_id,
        )
        return (await self._conn.execute(stmt)).first() is not None

    async def list_members(self, org_id: uuid.UUID) -> list[OrgMember]:
        """List the organization's members with their profile details, by name."""
        members, member_profiles = organization_members, profiles
        stmt = (
            sa.select(
                members.c.user_id,
                members.c.role,
                members.c.created_at.label("joined_at"),
                member_profiles.c.email,
                member_profiles.c.full_name,
                member_profiles.c.avatar_url,
                member_profiles.c.job_title,
            )
            .select_from(
                members.outerjoin(member_profiles, member_profiles.c.id == members.c.user_id)
            )
            .where(members.c.org_id == org_id)
            .order_by(
                sa.func.lower(
                    sa.func.coalesce(member_profiles.c.full_name, member_profiles.c.email)
                ),
                members.c.user_id,
            )
        )
        rows = (await self._conn.execute(stmt)).all()
        return [OrgMember.model_validate(dict(row._mapping)) for row in rows]

    async def memberships_for(self, user_id: uuid.UUID) -> list[OrganizationMembership]:
        """List the organizations the user belongs to, with their role, by name."""
        orgs, members = organizations, organization_members
        stmt = (
            sa.select(orgs.c.id, orgs.c.name, orgs.c.slug, members.c.role)
            .select_from(orgs.join(members, members.c.org_id == orgs.c.id))
            .where(members.c.user_id == user_id)
            .order_by(orgs.c.name, orgs.c.id)
        )
        rows = (await self._conn.execute(stmt)).all()
        return [OrganizationMembership.model_validate(dict(row._mapping)) for row in rows]
