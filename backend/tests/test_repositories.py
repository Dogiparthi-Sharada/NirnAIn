"""Tests of the project, organization, profile and notification repositories' SQL.

Purpose: Prove the queries behind the core screens: keyset pagination and search with escaped
wildcards, archiving on status change, the dashboard counts, memberships and notifications.
How it fits: Compiles the statements of app/repositories (projects, orgs, me, notifications) for
PostgreSQL through the recording connection of tests/support/connections.py; layer: tests.
Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from app.core.pagination import Cursor
from app.domain.common import ProjectStatus
from app.repositories.me import ProfileRepository
from app.repositories.notifications import NotificationRepository
from app.repositories.orgs import OrgRepository
from app.repositories.projects import ProjectRepository, escape_like
from tests.support.connections import Conn, Result, Rows

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def project_row(**overrides: Any) -> dict[str, Any]:
    """Return a project row, changed by `overrides`."""
    row = {
        "id": uuid.uuid4(),
        "org_id": uuid.uuid4(),
        "code": "P-001",
        "name": "CRM",
        "description": None,
        "type": "technology",
        "department": None,
        "sponsor": None,
        "pm_user_id": None,
        "pm_name": None,
        "priority": "medium",
        "methodology": "waterfall",
        "visibility": "internal",
        "start_date": date(2026, 10, 1),
        "target_end_date": None,
        "budget_cents": 100,
        "currency": "USD",
        "status": "draft",
        "current_step": "create",
        "progress_pct": 0,
        "created_by": None,
        "created_at": NOW,
        "updated_at": NOW,
        "archived_at": None,
    }
    row.update(overrides)
    return row


def test_escape_like() -> None:
    """LIKE wildcards and backslashes in a search are escaped."""
    assert escape_like(r"50%_off\x") == r"50\%\_off\\x"


async def test_project_list_sql() -> None:
    """The list joins the PM, hides archived, searches and pages by cursor (one extra row)."""
    rows = [project_row(), project_row(), project_row()]
    conn = Conn(Result(rows))
    repo = ProjectRepository(conn)  # type: ignore[arg-type]
    cursor = Cursor(NOW, uuid.uuid4())
    items, next_cursor = await repo.list_page(
        uuid.uuid4(), statuses=None, search="100%", limit=2, cursor=cursor
    )
    sql = conn.sql[0]
    assert "LEFT OUTER JOIN public.profiles AS pm" in sql
    assert "public.projects.status != " in sql
    assert "ILIKE" in sql
    assert "(public.projects.created_at, public.projects.id) < " in sql
    assert "ORDER BY public.projects.created_at DESC, public.projects.id DESC" in sql
    assert "LIMIT" in sql
    assert conn.params[0]["param_1"] == 3 or 3 in conn.params[0].values()
    assert "%100\\%%" in conn.params[0].values()
    assert len(items) == 2
    assert next_cursor == Cursor(items[1].created_at, items[1].id)


async def test_project_list_status_filter() -> None:
    """A status filter lists exactly those statuses; a short page has no next cursor."""
    conn = Conn(Result([project_row()]))
    repo = ProjectRepository(conn)  # type: ignore[arg-type]
    items, next_cursor = await repo.list_page(
        uuid.uuid4(), statuses=[ProjectStatus.ARCHIVED], search=None, limit=5, cursor=None
    )
    assert "public.projects.status IN" in conn.sql[0]
    assert next_cursor is None
    assert len(items) == 1


async def test_project_create_generates_id_without_returning() -> None:
    """A project gets its id in Python, so the insert needs no RETURNING under RLS."""
    conn = Conn(Result([]))
    repo = ProjectRepository(conn)  # type: ignore[arg-type]
    creator = uuid.uuid4()
    project_id = await repo.create(uuid.uuid4(), creator, {"name": "X", "currency": "USD"})
    assert isinstance(project_id, uuid.UUID)
    assert conn.sql[0].startswith("INSERT INTO public.projects")
    assert "RETURNING" not in conn.sql[0]
    assert conn.params[0]["id"] == project_id
    assert conn.params[0]["created_by"] == creator


async def test_project_update_archives() -> None:
    """Archiving stamps the time once; other statuses clear it; other edits leave it."""
    conn = Conn(Result([], rowcount=1), Result([], rowcount=0), Result([], rowcount=1))
    repo = ProjectRepository(conn)  # type: ignore[arg-type]
    assert await repo.update(uuid.uuid4(), {"status": ProjectStatus.ARCHIVED}) is True
    assert "archived_at=coalesce(public.projects.archived_at, now())" in conn.sql[0]
    assert await repo.update(uuid.uuid4(), {"status": ProjectStatus.ON_TRACK}) is False
    assert "archived_at=" in conn.sql[1]
    assert await repo.update(uuid.uuid4(), {"name": "N"}) is True
    assert "archived_at" not in conn.sql[2]


async def test_project_update_mirrors_dates_on_step_5() -> None:
    """New dates also update the constraint set (start, must-finish-by) in the same transaction;
    a cleared date keeps step 5's, other edits and refused updates write nothing there."""
    project_id = uuid.uuid4()
    conn = Conn(Result([], rowcount=1), Result([], rowcount=1))
    repo = ProjectRepository(conn)  # type: ignore[arg-type]
    changes = {"start_date": date(2026, 12, 1), "target_end_date": date(2027, 5, 28)}
    assert await repo.update(project_id, changes) is True
    assert conn.sql[1].startswith("UPDATE public.constraint_sets SET start_date=")
    assert "must_finish_by=" in conn.sql[1]
    assert (conn.params[1]["start_date"], conn.params[1]["must_finish_by"]) == (
        date(2026, 12, 1),
        date(2027, 5, 28),
    )
    assert conn.params[1]["project_id_1"] == project_id

    cleared = Conn(Result([], rowcount=1), Result([], rowcount=1))
    await ProjectRepository(cleared).update(  # type: ignore[arg-type]
        project_id, {"start_date": date(2026, 12, 1), "target_end_date": None}
    )
    assert "must_finish_by" not in cleared.sql[1]
    other = Conn(Result([], rowcount=1))
    await ProjectRepository(other).update(project_id, {"name": "N"})  # type: ignore[arg-type]
    refused = Conn(Result([], rowcount=0))
    assert await ProjectRepository(refused).update(project_id, changes) is False  # type: ignore[arg-type]
    assert len(other.sql) == len(refused.sql) == 1


async def test_project_step5_dates() -> None:
    """Step 5's saved dates are read from the constraint set; None before it is saved."""
    saved = Conn(Rows([{"start_date": date(2026, 12, 1), "must_finish_by": None}]))
    repo = ProjectRepository(saved)  # type: ignore[arg-type]
    assert await repo.step5_dates(uuid.uuid4()) == (date(2026, 12, 1), None)
    assert "FROM public.constraint_sets" in saved.sql[0]
    assert await ProjectRepository(Conn(Result([]))).step5_dates(uuid.uuid4()) is None  # type: ignore[arg-type]


async def test_project_get_and_delete() -> None:
    """A project is read by id and deleted; a missing one is None."""
    row = project_row()
    conn = Conn(Result([row]), Result([], rowcount=1), Result([]))
    repo = ProjectRepository(conn)  # type: ignore[arg-type]
    project = await repo.get(row["id"])
    assert project is not None
    assert project.id == row["id"]
    assert await repo.delete(row["id"]) is True
    assert conn.sql[1].startswith("DELETE FROM public.projects")
    assert await repo.get(uuid.uuid4()) is None


async def test_dashboard_sql() -> None:
    """The dashboard counts with filters over active projects in one query."""
    conn = Conn(
        Result(
            [
                {
                    "total_projects": 3,
                    "on_track": 1,
                    "at_risk_or_delayed": 1,
                    "total_budget_cents": 500,
                    "created_this_month": 2,
                }
            ]
        )
    )
    summary = await ProjectRepository(conn).dashboard(uuid.uuid4())  # type: ignore[arg-type]
    sql = conn.sql[0]
    assert "count(*) FILTER (WHERE" in sql
    assert "public.projects.archived_at IS NULL" in sql
    assert "date_trunc" in sql
    assert summary.total_budget_cents == 500


async def test_org_repository() -> None:
    """Visibility, membership, members (with profiles) and the user's organizations are read."""
    user_id, org_id = uuid.uuid4(), uuid.uuid4()
    conn = Conn(
        Result([{"one": 1}]),
        Result([]),
        Result(
            [
                {
                    "user_id": user_id,
                    "role": "owner",
                    "joined_at": NOW,
                    "email": "a@b.c",
                    "full_name": "A",
                    "avatar_url": None,
                    "job_title": None,
                }
            ]
        ),
        Result([{"id": org_id, "name": "Acme", "slug": "acme", "role": "admin"}]),
    )
    repo = OrgRepository(conn)  # type: ignore[arg-type]
    assert await repo.is_visible(org_id) is True
    assert await repo.is_member(org_id, user_id) is False
    members = await repo.list_members(org_id)
    assert members[0].full_name == "A"
    assert "LEFT OUTER JOIN public.profiles" in conn.sql[2]
    orgs = await repo.memberships_for(user_id)
    assert orgs[0].role == "admin"


async def test_profile_repository() -> None:
    """A profile is read and updated."""
    user_id = uuid.uuid4()
    conn = Conn(
        Result(
            [
                {
                    "id": user_id,
                    "email": "a@b.c",
                    "full_name": None,
                    "avatar_url": None,
                    "job_title": None,
                }
            ]
        ),
        Result([], rowcount=1),
    )
    repo = ProfileRepository(conn)  # type: ignore[arg-type]
    profile = await repo.get(user_id)
    assert profile is not None
    assert profile.email == "a@b.c"
    assert await repo.update(user_id, {"full_name": "B"}) is True
    assert conn.sql[1].startswith("UPDATE public.profiles SET full_name=")


async def test_notification_repository() -> None:
    """Notifications page by cursor and filter by read state; marking read keeps the first time."""
    user_id = uuid.uuid4()
    row = {
        "id": uuid.uuid4(),
        "org_id": uuid.uuid4(),
        "project_id": None,
        "level": "info",
        "message": "hi",
        "read_at": None,
        "created_at": NOW,
    }
    conn = Conn(Result([row, row]), Result([], rowcount=1), Result([row]), Result([]))
    repo = NotificationRepository(conn)  # type: ignore[arg-type]
    items, next_cursor = await repo.list_page(
        user_id, unread=True, limit=1, cursor=Cursor(NOW, uuid.uuid4())
    )
    assert "read_at IS NULL" in conn.sql[0]
    assert "public.notifications.user_id = " in conn.sql[0]
    assert len(items) == 1
    assert next_cursor is not None
    assert await repo.mark_read(row["id"], user_id) is True
    assert "read_at=coalesce(public.notifications.read_at, now())" in conn.sql[1]
    assert await repo.get(row["id"]) is not None
    _, _ = await repo.list_page(user_id, unread=False, limit=10, cursor=None)
    assert "read_at IS NOT NULL" in conn.sql[3]
