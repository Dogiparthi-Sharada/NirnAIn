"""In-memory model of the core tables and the RLS rules the API relies on.

Purpose: Run the endpoint tests without Postgres while keeping its visibility rules: members see
their organizations and projects, viewers can't write, and denied writes raise the SQLSTATE 42501
error RLS would.
How it fits: The `store` fixture (tests/conftest.py) creates one per test; the fake repositories
below replace the real ones through FastAPI dependency overrides; layer: tests.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.pagination import Cursor
from app.domain.common import OrgRole, ProjectStatus
from app.domain.dashboard import DashboardSummary
from app.domain.me import OrganizationMembership, Profile
from app.domain.notifications import Notification
from app.domain.orgs import OrgMember
from app.domain.projects import Project


# --------------------------------------------------------------------------- in-memory fakes
@dataclass
class Store:
    """Tiny in-memory model of the tables plus the RLS rules we rely on."""

    profiles: dict[uuid.UUID, Profile] = field(default_factory=dict)
    orgs: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    members: dict[tuple[uuid.UUID, uuid.UUID], OrgRole] = field(default_factory=dict)
    projects: dict[uuid.UUID, Project] = field(default_factory=dict)
    notifications: dict[uuid.UUID, tuple[uuid.UUID, Notification]] = field(default_factory=dict)
    next_code: int = 1

    def role(self, org_id: uuid.UUID, user_id: uuid.UUID) -> OrgRole | None:
        """Return the user's role in the organization, or None when they are not a member."""
        return self.members.get((org_id, user_id))

    def add_user(self, name: str = "Jane Doe") -> uuid.UUID:
        """Add a user profile named `name` (email from the first name) and return its id."""
        user_id = uuid.uuid4()
        self.profiles[user_id] = Profile(
            id=user_id,
            email=f"{name.split(maxsplit=1)[0].lower()}@example.com",
            full_name=name,
            avatar_url=None,
            job_title=None,
        )
        return user_id

    def add_org(self, owner: uuid.UUID, name: str = "Acme") -> uuid.UUID:
        """Add an organization with `owner` as its owner and return its id."""
        org_id = uuid.uuid4()
        self.orgs[org_id] = {"id": org_id, "name": name, "slug": name.lower()}
        self.members[(org_id, owner)] = OrgRole.OWNER
        return org_id


class FakeProfiles:
    """Stand-in for `ProfileRepository` over the store."""

    def __init__(self, store: Store) -> None:
        """Use the shared in-memory store."""
        self.store = store

    async def get(self, user_id: uuid.UUID) -> Profile | None:
        """Return the user's profile, or None."""
        return self.store.profiles.get(user_id)

    async def update(self, user_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        """Apply the changes to the profile; False when there is none."""
        profile = self.store.profiles.get(user_id)
        if profile is None:
            return False
        self.store.profiles[user_id] = profile.model_copy(update=changes)
        return True


class FakeOrgs:
    """Stand-in for `OrgRepository` as seen by one user (membership = visibility)."""

    def __init__(self, store: Store, user_id: uuid.UUID) -> None:
        """Act as the given user over the shared store."""
        self.store, self.user_id = store, user_id

    async def is_visible(self, org_id: uuid.UUID) -> bool:
        """Tell whether the acting user is a member of the organization."""
        return self.store.role(org_id, self.user_id) is not None

    async def is_member(self, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Tell whether `user_id` is a member of an organization the acting user can see."""
        return await self.is_visible(org_id) and self.store.role(org_id, user_id) is not None

    async def list_members(self, org_id: uuid.UUID) -> list[OrgMember]:
        """Return the organization's members with their profile names."""
        out = []
        for (member_org_id, member_id), role in self.store.members.items():
            if member_org_id == org_id:
                profile = self.store.profiles.get(member_id)
                out.append(
                    OrgMember(
                        user_id=member_id,
                        role=role,
                        email=profile.email if profile else None,
                        full_name=profile.full_name if profile else None,
                        avatar_url=None,
                        job_title=None,
                        joined_at=datetime.now(UTC),
                    )
                )
        return out

    async def memberships_for(self, user_id: uuid.UUID) -> list[OrganizationMembership]:
        """Return the organizations the user belongs to, with their role."""
        return [
            OrganizationMembership(
                id=member_org_id,
                name=self.store.orgs[member_org_id]["name"],
                slug=self.store.orgs[member_org_id]["slug"],
                role=role,
            )
            for (member_org_id, member_id), role in self.store.members.items()
            if member_id == user_id
        ]


class FakeProjects:
    """Stand-in for `ProjectRepository` with the projects' RLS rules."""

    def __init__(self, store: Store, user_id: uuid.UUID) -> None:
        """Act as the given user over the shared store."""
        self.store, self.user_id = store, user_id

    def _readable(self, project: Project) -> bool:
        """Tell whether the acting user may see the project (a member of its organization)."""
        return self.store.role(project.org_id, self.user_id) is not None

    async def list_page(
        self,
        org_id: uuid.UUID,
        *,
        statuses: list[ProjectStatus] | None,
        search: str | None,
        limit: int,
        cursor: Cursor | None,
    ) -> tuple[list[Project], Cursor | None]:
        """Return a page of visible projects, newest first, filtered like the real repository."""
        rows = [
            project
            for project in self.store.projects.values()
            if project.org_id == org_id and self._readable(project)
        ]
        if statuses:
            rows = [project for project in rows if project.status in statuses]
        else:
            rows = [project for project in rows if project.status != ProjectStatus.ARCHIVED]
        if search:
            rows = [
                project
                for project in rows
                if search.lower() in project.name.lower() or search.lower() in project.code.lower()
            ]
        rows.sort(key=lambda project: (project.created_at, project.id), reverse=True)
        if cursor:
            rows = [
                project
                for project in rows
                if (project.created_at, project.id) < (cursor.created_at, cursor.id)
            ]
        page = rows[:limit]
        next_cursor = Cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
        return page, next_cursor

    async def get(self, project_id: uuid.UUID) -> Project | None:
        """Return a visible project, or None."""
        project = self.store.projects.get(project_id)
        return project if project and self._readable(project) else None

    async def create(
        self, org_id: uuid.UUID, created_by: uuid.UUID, values: dict[str, Any]
    ) -> uuid.UUID:
        """Insert a draft project with the next code; viewers and outsiders get an RLS error."""
        role = self.store.role(org_id, self.user_id)
        if role is None or role == OrgRole.VIEWER:
            raise rls_violation()
        project_id = uuid.uuid4()
        now = datetime.now(UTC) + timedelta(microseconds=len(self.store.projects))
        manager = (
            self.store.profiles.get(values["pm_user_id"]) if values.get("pm_user_id") else None
        )
        values = {"status": ProjectStatus.DRAFT, "current_step": "create", **values}
        self.store.projects[project_id] = Project(
            id=project_id,
            org_id=org_id,
            code=f"P-{self.store.next_code:03d}",
            pm_name=manager.full_name if manager else None,
            progress_pct=0,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            archived_at=None,
            **values,
        )
        self.store.next_code += 1
        return project_id

    async def update(self, project_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        """Apply changes when the acting user may write; else False, as RLS filters it."""
        project = self.store.projects.get(project_id)
        if project is None or self.store.role(project.org_id, self.user_id) in (
            None,
            OrgRole.VIEWER,
        ):
            return False
        self.store.projects[project_id] = project.model_copy(update=changes)
        return True

    async def delete(self, project_id: uuid.UUID) -> bool:
        """Delete when the acting user is owner, admin or the creator; False otherwise."""
        project = self.store.projects.get(project_id)
        if project is None:
            return False
        role = self.store.role(project.org_id, self.user_id)
        if role in (OrgRole.OWNER, OrgRole.ADMIN) or project.created_by == self.user_id:
            del self.store.projects[project_id]
            return True
        return False

    async def dashboard(self, org_id: uuid.UUID) -> DashboardSummary:
        """Return the KPI cards over the organization's non-archived projects."""
        rows = [
            project
            for project in self.store.projects.values()
            if project.org_id == org_id and project.status != ProjectStatus.ARCHIVED
        ]
        return DashboardSummary(
            total_projects=len(rows),
            on_track=sum(project.status == ProjectStatus.ON_TRACK for project in rows),
            at_risk_or_delayed=sum(
                project.status in (ProjectStatus.AT_RISK, ProjectStatus.DELAYED) for project in rows
            ),
            total_budget_cents=sum(project.budget_cents or 0 for project in rows),
            created_this_month=len(rows),
        )


class FakeNotifications:
    """Stand-in for `NotificationRepository`: users see only their own notifications."""

    def __init__(self, store: Store, user_id: uuid.UUID) -> None:
        """Act as the given user over the shared store."""
        self.store, self.user_id = store, user_id

    async def list_page(
        self, user_id: uuid.UUID, *, unread: bool | None, limit: int, cursor: Cursor | None
    ) -> tuple[list[Notification], Cursor | None]:
        """Return a page of the user's notifications, newest first, optionally unread/read."""
        rows = [
            notification
            for owner_id, notification in self.store.notifications.values()
            if owner_id == user_id
        ]
        if unread is not None:
            rows = [
                notification for notification in rows if (notification.read_at is None) == unread
            ]
        rows.sort(key=lambda notification: (notification.created_at, notification.id), reverse=True)
        if cursor:
            rows = [
                notification
                for notification in rows
                if (notification.created_at, notification.id) < (cursor.created_at, cursor.id)
            ]
        page = rows[:limit]
        return page, (Cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None)

    async def get(self, notification_id: uuid.UUID) -> Notification | None:
        """Return one of the acting user's notifications, or None."""
        entry = self.store.notifications.get(notification_id)
        return entry[1] if entry and entry[0] == self.user_id else None

    async def mark_read(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Mark the user's notification read (keeping the first read time); else False."""
        entry = self.store.notifications.get(notification_id)
        if entry is None or entry[0] != user_id:
            return False
        notification = entry[1]
        if notification.read_at is None:
            notification = notification.model_copy(update={"read_at": datetime.now(UTC)})
        self.store.notifications[notification_id] = (user_id, notification)
        return True


def rls_violation() -> Exception:
    """A DBAPIError shaped like asyncpg's InsufficientPrivilegeError."""
    from sqlalchemy.exc import DBAPIError

    class _OrigError(Exception):
        sqlstate = "42501"
        message = 'new row violates row-level security policy for table "projects"'

    return DBAPIError("INSERT ...", {}, _OrigError())


class ProjectScopedFake:
    """Base of the fake repositories that act as one user over the store's projects."""

    def __init__(self, store: Store, user_id: uuid.UUID) -> None:
        """Act as `user_id` over the shared store."""
        self.store = store
        self.user_id = user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        """Return the acting user's role in the project's organization (None: no access)."""
        project = self.store.projects.get(project_id)
        return self.store.role(project.org_id, self.user_id) if project else None

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        """Tell whether the acting user can see the project."""
        return self._role(project_id) is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        """Tell whether the acting user may change the project (members other than viewers)."""
        return self._role(project_id) not in (None, OrgRole.VIEWER)
