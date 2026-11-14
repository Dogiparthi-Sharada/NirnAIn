"""Moved test support."""

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
        return self.members.get((org_id, user_id))

    def add_user(self, name: str = "Jane Doe") -> uuid.UUID:
        uid = uuid.uuid4()
        self.profiles[uid] = Profile(
            id=uid,
            email=f"{name.split(maxsplit=1)[0].lower()}@example.com",
            full_name=name,
            avatar_url=None,
            job_title=None,
        )
        return uid

    def add_org(self, owner: uuid.UUID, name: str = "Acme") -> uuid.UUID:
        oid = uuid.uuid4()
        self.orgs[oid] = {"id": oid, "name": name, "slug": name.lower()}
        self.members[(oid, owner)] = OrgRole.OWNER
        return oid


class FakeProfiles:
    def __init__(self, store: Store) -> None:
        self.s = store

    async def get(self, user_id: uuid.UUID) -> Profile | None:
        return self.s.profiles.get(user_id)

    async def update(self, user_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        p = self.s.profiles.get(user_id)
        if p is None:
            return False
        self.s.profiles[user_id] = p.model_copy(update=changes)
        return True


class FakeOrgs:
    def __init__(self, store: Store, user_id: uuid.UUID) -> None:
        self.s, self.uid = store, user_id

    async def is_visible(self, org_id: uuid.UUID) -> bool:
        return self.s.role(org_id, self.uid) is not None

    async def is_member(self, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        return await self.is_visible(org_id) and self.s.role(org_id, user_id) is not None

    async def list_members(self, org_id: uuid.UUID) -> list[OrgMember]:
        out = []
        for (oid, uid), role in self.s.members.items():
            if oid == org_id:
                p = self.s.profiles.get(uid)
                out.append(
                    OrgMember(
                        user_id=uid,
                        role=role,
                        email=p.email if p else None,
                        full_name=p.full_name if p else None,
                        avatar_url=None,
                        job_title=None,
                        joined_at=datetime.now(UTC),
                    )
                )
        return out

    async def memberships_for(self, user_id: uuid.UUID) -> list[OrganizationMembership]:
        return [
            OrganizationMembership(
                id=oid, name=self.s.orgs[oid]["name"], slug=self.s.orgs[oid]["slug"], role=role
            )
            for (oid, uid), role in self.s.members.items()
            if uid == user_id
        ]


class FakeProjects:
    def __init__(self, store: Store, user_id: uuid.UUID) -> None:
        self.s, self.uid = store, user_id

    def _readable(self, p: Project) -> bool:
        return self.s.role(p.org_id, self.uid) is not None

    async def list_page(
        self,
        org_id: uuid.UUID,
        *,
        statuses: list[ProjectStatus] | None,
        search: str | None,
        limit: int,
        cursor: Cursor | None,
    ) -> tuple[list[Project], Cursor | None]:
        rows = [p for p in self.s.projects.values() if p.org_id == org_id and self._readable(p)]
        if statuses:
            rows = [p for p in rows if p.status in statuses]
        else:
            rows = [p for p in rows if p.status != ProjectStatus.ARCHIVED]
        if search:
            rows = [
                p
                for p in rows
                if search.lower() in p.name.lower() or search.lower() in p.code.lower()
            ]
        rows.sort(key=lambda p: (p.created_at, p.id), reverse=True)
        if cursor:
            rows = [p for p in rows if (p.created_at, p.id) < (cursor.created_at, cursor.id)]
        page = rows[:limit]
        nxt = Cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
        return page, nxt

    async def get(self, project_id: uuid.UUID) -> Project | None:
        p = self.s.projects.get(project_id)
        return p if p and self._readable(p) else None

    async def create(
        self, org_id: uuid.UUID, created_by: uuid.UUID, values: dict[str, Any]
    ) -> uuid.UUID:
        role = self.s.role(org_id, self.uid)
        if role is None or role == OrgRole.VIEWER:
            raise rls_violation()
        pid = uuid.uuid4()
        now = datetime.now(UTC) + timedelta(microseconds=len(self.s.projects))
        pm = self.s.profiles.get(values["pm_user_id"]) if values.get("pm_user_id") else None
        values = {"status": ProjectStatus.DRAFT, "current_step": "create", **values}
        self.s.projects[pid] = Project(
            id=pid,
            org_id=org_id,
            code=f"P-{self.s.next_code:03d}",
            pm_name=pm.full_name if pm else None,
            progress_pct=0,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            archived_at=None,
            **values,
        )
        self.s.next_code += 1
        return pid

    async def update(self, project_id: uuid.UUID, changes: dict[str, Any]) -> bool:
        p = self.s.projects.get(project_id)
        if p is None or self.s.role(p.org_id, self.uid) in (None, OrgRole.VIEWER):
            return False
        self.s.projects[project_id] = p.model_copy(update=changes)
        return True

    async def delete(self, project_id: uuid.UUID) -> bool:
        p = self.s.projects.get(project_id)
        if p is None:
            return False
        role = self.s.role(p.org_id, self.uid)
        if role in (OrgRole.OWNER, OrgRole.ADMIN) or p.created_by == self.uid:
            del self.s.projects[project_id]
            return True
        return False

    async def dashboard(self, org_id: uuid.UUID) -> DashboardSummary:
        rows = [
            p
            for p in self.s.projects.values()
            if p.org_id == org_id and p.status != ProjectStatus.ARCHIVED
        ]
        return DashboardSummary(
            total_projects=len(rows),
            on_track=sum(p.status == ProjectStatus.ON_TRACK for p in rows),
            at_risk_or_delayed=sum(
                p.status in (ProjectStatus.AT_RISK, ProjectStatus.DELAYED) for p in rows
            ),
            total_budget_cents=sum(p.budget_cents or 0 for p in rows),
            created_this_month=len(rows),
        )


class FakeNotifications:
    def __init__(self, store: Store, user_id: uuid.UUID) -> None:
        self.s, self.uid = store, user_id

    async def list_page(
        self, user_id: uuid.UUID, *, unread: bool | None, limit: int, cursor: Cursor | None
    ) -> tuple[list[Notification], Cursor | None]:
        rows = [n for uid, n in self.s.notifications.values() if uid == user_id]
        if unread is not None:
            rows = [n for n in rows if (n.read_at is None) == unread]
        rows.sort(key=lambda n: (n.created_at, n.id), reverse=True)
        if cursor:
            rows = [n for n in rows if (n.created_at, n.id) < (cursor.created_at, cursor.id)]
        page = rows[:limit]
        return page, (Cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None)

    async def get(self, notification_id: uuid.UUID) -> Notification | None:
        entry = self.s.notifications.get(notification_id)
        return entry[1] if entry and entry[0] == self.uid else None

    async def mark_read(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        entry = self.s.notifications.get(notification_id)
        if entry is None or entry[0] != user_id:
            return False
        n = entry[1]
        if n.read_at is None:
            n = n.model_copy(update={"read_at": datetime.now(UTC)})
        self.s.notifications[notification_id] = (user_id, n)
        return True


def rls_violation() -> Exception:
    """A DBAPIError shaped like asyncpg's InsufficientPrivilegeError."""
    from sqlalchemy.exc import DBAPIError

    class _OrigError(Exception):
        sqlstate = "42501"
        message = 'new row violates row-level security policy for table "projects"'

    return DBAPIError("INSERT ...", {}, _OrigError())
