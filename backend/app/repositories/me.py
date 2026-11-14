"""Repository of the signed-in user's own profile.

Purpose: Read and update the caller's `profiles` row; RLS lets users touch only their own.
How it fits: Used by app/api/v1/me.py; returns app/domain/me.py models; layer: repositories.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.me import Profile
from app.repositories.tables import profiles


class ProfileRepository:
    """Reads and updates the caller's profile row."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def get(self, user_id: uuid.UUID) -> Profile | None:
        """Return the user's profile, or None when it is missing or not visible."""
        stmt = sa.select(
            profiles.c.id,
            profiles.c.email,
            profiles.c.full_name,
            profiles.c.avatar_url,
            profiles.c.job_title,
        ).where(profiles.c.id == user_id)
        row = (await self._conn.execute(stmt)).first()
        return Profile.model_validate(dict(row._mapping)) if row else None

    async def update(self, user_id: uuid.UUID, changes: dict[str, str | None]) -> bool:
        """Update the profile columns given; False when no row was changed."""
        stmt = sa.update(profiles).where(profiles.c.id == user_id).values(**changes)
        result = await self._conn.execute(stmt)
        return result.rowcount > 0
