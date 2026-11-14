"""Endpoints of the signed-in user: profile and organizations.

Purpose: Let the app show who is signed in and let them edit their own name and job title.
How it fits: Reads and writes app/repositories/me.py and app/repositories/orgs.py; layer: api.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Orgs, Profiles
from app.core.errors import NotFoundError
from app.core.security import CurrentPrincipal
from app.domain.me import Me, MeUpdate

router = APIRouter(prefix="/me", tags=["me"])


async def _load_me(principal: CurrentPrincipal, profiles: Profiles, orgs: Orgs) -> Me:
    """Return the caller's profile with their organization memberships; 404 without a profile."""
    profile = await profiles.get(principal.user_id)
    if profile is None:
        raise NotFoundError("Profile not found")
    memberships = await orgs.memberships_for(principal.user_id)
    return Me(**profile.model_dump(by_alias=False), organizations=memberships)


@router.get("", response_model=Me, summary="Current user profile and organizations")
async def get_me(principal: CurrentPrincipal, profiles: Profiles, orgs: Orgs) -> Me:
    """Return the signed-in user's profile and organizations."""
    return await _load_me(principal, profiles, orgs)


@router.patch("", response_model=Me, summary="Update the current user's profile")
async def update_me(
    body: MeUpdate, principal: CurrentPrincipal, profiles: Profiles, orgs: Orgs
) -> Me:
    """Update the signed-in user's profile and return it; 404 without a profile."""
    if not await profiles.update(principal.user_id, body.changes()):
        raise NotFoundError("Profile not found")
    return await _load_me(principal, profiles, orgs)
