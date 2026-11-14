"""API model of an in-app notification.

Purpose: Tell project members about events that need attention (runs finished, changes, risks).
How it fits: Listed by app/api/v1/notifications.py through app/repositories/notifications.py and
shown on the project overview; layer: domain.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.domain.common import CamelModel, NotificationLevel


class Notification(CamelModel):
    """A notification for an organization, optionally about one project."""

    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID | None
    level: NotificationLevel
    message: str
    read_at: datetime | None
    created_at: datetime
