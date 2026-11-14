"""API model of an organization member.

Purpose: List who is in an organization and with which role (for PM pickers and team setup).
How it fits: Returned by app/api/v1/orgs.py from app/repositories/orgs.py; layer: domain.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.domain.common import CamelModel, OrgRole


class OrgMember(CamelModel):
    """A member of the organization with their role and profile details."""

    user_id: uuid.UUID
    role: OrgRole
    email: str | None
    full_name: str | None
    avatar_url: str | None
    job_title: str | None
    joined_at: datetime
