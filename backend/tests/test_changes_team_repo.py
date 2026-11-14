"""SQL of the team writes (app/repositories/change_team.py, team.py) on a recording connection.

What: reserving a newcomer (an inactive candidate employee), releasing unused candidates,
approving a change that adds Dana and removes Sam on a project still planned with the
organization's pool, and taking someone off the team on step 5 (RES-017). Why: these writes
change the project's team only on approval, must never delete a real employee, and leave
nobody off the team owning a work package; there is no database in the test run. How it
fits: compiles every statement for PostgreSQL through the `Conn` recorder of
tests/test_repositories.py.
"""

from __future__ import annotations

import uuid
from datetime import date

from app.domain.changes import AddedMember, DetectedChange, NewMember, PlanOverrides
from app.repositories.access import ProjectRef
from app.repositories.changes import ChangeRepository
from app.repositories.team import TeamRepository
from tests.test_optimization_repo import Rows
from tests.test_repositories import Conn, Result

PROJECT = ProjectRef(uuid.uuid4(), uuid.uuid4(), "EDP", date(2026, 11, 30), None, None)
DANA = NewMember(
    full_name="Dana Lee",
    role_title="Security Specialist",
    hours_per_week=24,
    skills=["Security & Compliance Review"],
    worker_type="contractor",
    hourly_rate_cents=17_500,
)


def _repository(conn: Conn) -> ChangeRepository:
    return ChangeRepository(conn)  # type: ignore[arg-type]


def _starts(statements: list[str]) -> list[str]:
    """Each statement's verb and table, e.g. "DELETE FROM public.employees"."""
    return [" ".join(statement.split()[:3]) for statement in statements]


async def test_a_newcomer_is_reserved_as_an_inactive_candidate() -> None:
    conn = Conn(Rows([]))  # nobody in the organization is called Dana Lee
    addition = DetectedChange(
        id="c1", kind="team", action="add", target_label="Dana Lee", to=24, member=DANA
    )
    [reserved] = await _repository(conn).reserve_members(PROJECT, [addition])
    assert reserved.target is not None
    assert "lower(public.employees.full_name)" in conn.sql[0]
    assert "public.employees.active IS true" in conn.sql[0]
    assert _starts(conn.sql)[1] == "INSERT INTO public.employees"
    assert {
        key: conn.params[1][key]
        for key in ("id", "full_name", "active", "is_contractor", "hourly_rate_cents")
    } == {
        "id": reserved.target,
        "full_name": "Dana Lee",
        "active": False,
        "is_contractor": True,
        "hourly_rate_cents": 17_500,
    }

    existing = uuid.uuid4()
    conn = Conn(Rows([{"id": existing}]))
    [reused] = await _repository(conn).reserve_members(PROJECT, [addition])
    assert reused.target == existing
    assert len(conn.sql) == 1  # nothing created


async def test_release_only_deletes_unused_inactive_candidates() -> None:
    conn = Conn()
    await _repository(conn).release_members(PROJECT, [uuid.uuid4()])
    [statement] = conn.sql
    assert _starts(conn.sql) == ["DELETE FROM public.employees"]
    assert "public.employees.active IS false" in statement
    assert "NOT (EXISTS (SELECT * \nFROM public.project_team_members" in statement
    assert "NOT (EXISTS (SELECT * \nFROM public.allocations" in statement
    await _repository(conn).release_members(PROJECT, [])
    assert len(conn.sql) == 1


async def test_approval_writes_who_joins_and_who_leaves() -> None:
    sam, dana, skill = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = Conn(
        Result([]),  # no team yet: the organization's pool becomes the team first
        *[Result([]) for _ in range(6)],
        Rows([{"id": skill, "name": "Security & Compliance Review"}]),
    )
    overrides = PlanOverrides(
        add_members=[AddedMember(employee_id=dana, **DANA.model_dump(by_alias=False))],
        remove_members=[sam],
    )
    await _repository(conn).write_overrides(PROJECT, overrides)
    assert _starts(conn.sql) == [
        "SELECT public.project_team_members.employee_id FROM",
        "INSERT INTO public.project_team_members",
        "DELETE FROM public.project_team_members",
        "UPDATE public.wbs_nodes SET",
        "UPDATE public.employees SET",
        "INSERT INTO public.project_team_members",
        "INSERT INTO public.skills",
        "SELECT public.skills.id, public.skills.name",
        "INSERT INTO public.employee_skills",
    ]
    pool, removal, ownership, activation, joining = conn.params[1:6]
    assert "FROM public.employees" in conn.sql[1]
    assert "public.employees.active IS true" in conn.sql[1]
    assert pool["param_1"] == PROJECT.id
    assert removal["employee_id_1"] == [sam]
    # Packages Sam still owns after the plan reassigned his work lose their owner.
    assert (ownership["owner_employee_id"], ownership["owner_employee_id_1"]) == (None, [sam])
    assert (activation["active"], activation["hourly_rate_cents"], activation["id_1"]) == (
        True,
        17_500,
        dana,
    )
    assert "ON CONFLICT (project_id, employee_id) DO UPDATE" in conn.sql[5]
    assert (joining["employee_id"], joining["hours_per_week"], joining["role_title"]) == (
        dana,
        24,
        "Security Specialist",
    )
    assert (conn.params[8]["employee_id_m0"], conn.params[8]["skill_id_m0"]) == (dana, skill)


async def test_approved_requirements_become_accepted_scope_items() -> None:
    conn = Conn()
    await _repository(conn).add_requirements(PROJECT.id, ["Workday as a data source"])
    assert _starts(conn.sql)[0] == "INSERT INTO public.scope_items"
    assert {key: conn.params[0][key] for key in ("category", "text", "status", "origin")} == {
        "category": "requirement",
        "text": "Workday as a data source",
        "status": "accepted",
        "origin": "manual",
    }


async def test_removing_a_member_on_step_5_unassigns_their_packages() -> None:
    """RES-017: the packages a removed person owns lose their owner, as on step 7."""
    mark = uuid.uuid4()
    conn = Conn(Result([]), Result([]))
    assert await TeamRepository(conn).remove_member(PROJECT.id, mark)  # type: ignore[arg-type]
    assert _starts(conn.sql) == [
        "DELETE FROM public.project_team_members",
        "UPDATE public.wbs_nodes SET",
    ]
    ownership = conn.params[1]
    assert (ownership["owner_employee_id"], ownership["owner_employee_id_1"]) == (None, mark)
    assert ownership["project_id_1"] == PROJECT.id

    nobody = Conn(Result([], rowcount=0))
    assert not await TeamRepository(nobody).remove_member(PROJECT.id, mark)  # type: ignore[arg-type]
    assert len(nobody.sql) == 1
