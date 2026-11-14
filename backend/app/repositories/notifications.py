"""Repository of the caller's in-app notifications.

Purpose: Page through a user's notifications (optionally unread or read only) with a keyset cursor,
read one and mark one read.
How it fits: Used by app/api/v1/notifications.py and the project overview; layer: repositories.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.pagination import Cursor
from app.domain.notifications import Notification
from app.repositories.tables import notifications

TIMESTAMP_TZ = sa.DateTime(timezone=True)
_COLUMNS = (
    notifications.c.id,
    notifications.c.org_id,
    notifications.c.project_id,
    notifications.c.level,
    notifications.c.message,
    notifications.c.read_at,
    notifications.c.created_at,
)


def _to_notification(row: sa.Row[Any]) -> Notification:
    """Turn a result row into the API model."""
    return Notification.model_validate(dict(row._mapping))


class NotificationRepository:
    """Notification queries; RLS limits them to the caller's own notifications."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def list_page(
        self,
        user_id: uuid.UUID,
        *,
        unread: bool | None,
        limit: int,
        cursor: Cursor | None,
    ) -> tuple[list[Notification], Cursor | None]:
        """Return one page of the user's notifications, newest first, and the next page's cursor.

        `unread` True/False filters on read state; None returns all.
        """
        table = notifications
        # RLS already restricts to the caller; the explicit filter uses the index.
        stmt = sa.select(*_COLUMNS).where(table.c.user_id == user_id)
        if unread is True:
            stmt = stmt.where(table.c.read_at.is_(None))
        elif unread is False:
            stmt = stmt.where(table.c.read_at.is_not(None))
        if cursor is not None:
            stmt = stmt.where(
                sa.tuple_(table.c.created_at, table.c.id)
                < sa.tuple_(
                    sa.literal(cursor.created_at, TIMESTAMP_TZ),
                    sa.literal(cursor.id, table.c.id.type),
                )
            )
        stmt = stmt.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(limit + 1)
        rows = (await self._conn.execute(stmt)).all()
        items = [_to_notification(row) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and items:
            next_cursor = Cursor(created_at=items[-1].created_at, id=items[-1].id)
        return items, next_cursor

    async def get(self, notification_id: uuid.UUID) -> Notification | None:
        """Return one notification, or None when missing or not the caller's."""
        stmt = sa.select(*_COLUMNS).where(notifications.c.id == notification_id)
        row = (await self._conn.execute(stmt)).first()
        return _to_notification(row) if row else None

    async def mark_read(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Mark it read (keeping the first read time); False when it is not the user's."""
        table = notifications
        stmt = (
            sa.update(table)
            .where(table.c.id == notification_id, table.c.user_id == user_id)
            .values(read_at=sa.func.coalesce(table.c.read_at, sa.func.now()))
        )
        result = await self._conn.execute(stmt)
        return result.rowcount > 0
