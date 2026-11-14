"""Required skills and the specialist answer reach the optimizer (round-2 decision D6).

The project's skills link to work packages (WBS tags first, else a keyword
match on the package's name and description); a Required skill no package
uses is a warning on the confirmation sheet; with the specialist answer Yes
and a rate, the work nobody on the team can do goes to a contractor at that
rate. Also covers how `PATCH /wbs/nodes/{nodeId}` stores a package's skills.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from app.optimization.explain import build_report
from app.optimization.inputs import SkillFact, facts_from_input
from app.optimization.model import SkillRequirement
from app.optimization.skill_links import PackageText, ProjectSkill, link_skills, mentions
from app.optimization.solver import solve
from app.repositories.optimization import OptimizationRepository
from app.repositories.wbs import WbsRepository
from tests.test_optimization_repo import Rows, project_ctx
from tests.test_optimization_solver import FAST
from tests.test_repositories import Conn

OKTA = ProjectSkill("okta", "Okta SSO", required=True)


def test_skills_link_by_wbs_tag_else_by_every_word_of_their_name() -> None:
    sign_in = PackageText("p1", "Staff sign-in", "SSO through okta for all staff")
    reports = PackageText("p2", "Reports", "Sales dashboards in Tableau")
    tagged_python = SkillRequirement("python", "Python", 4)
    assert mentions("Okta SSO", sign_in)
    assert not mentions("Okta SSO", PackageText("p3", "Okta admin console"))  # needs every word
    linked = link_skills(
        [sign_in, reports],
        [
            OKTA,
            ProjectSkill("tableau", "Tableau", required=False),  # Optional: never by keyword
            ProjectSkill("python", "Python", required=True),  # tagged on p2: no keyword links
        ],
        {"p2": [tagged_python]},
    )
    assert linked == {
        "p1": [SkillRequirement("okta", "Okta SSO", 3)],
        "p2": [tagged_python],
    }


def _node(
    node_id: uuid.UUID, parent: uuid.UUID | None, level: int, code: str, **fields: Any
) -> dict[str, Any]:
    return {
        "id": node_id,
        "parent_id": parent,
        "level": level,
        "name": f"Node {code}",
        "description": None,
        "duration_days": None,
        "effort_hours": None,
        "owner_employee_id": None,
        "code": code,
        **fields,
    }


async def test_qa_case_okta_required_and_a_specialist_at_185_reach_the_plan() -> None:
    """QA: "Okta SSO" Required, nobody has it, specialist Yes at a confirmed $185/h, and
    the plan ignored all of it. The package that mentions Okta SSO now needs the skill,
    so the specialist takes it, at that rate."""
    root, deliverable, sign_in, api = (uuid.uuid4() for _ in range(4))
    mark, okta = uuid.uuid4(), uuid.uuid4()
    constraint_set = {
        "start_date": None,
        "budget_total_cents": None,
        "labor_cap_cents": None,
        "budget_mode": "soft",
        "budget_penalty": 1000.0,
        "contingency_pct": 0,
        "must_finish_by": date(2027, 3, 31),
        "deadline_answer": "hard",
        "deadline_penalty_per_day": 500.0,
        "working_days_per_week": 5,
        "capacity_mode": "soft",
        "capacity_penalty": 100.0,
        "skill_mode": "hard",
        "contractor_rate_cents": 18_500,
        "specialist_answer": "yes",
        "specialist_rate_confirmed": True,
    }
    conn = Conn(
        Rows(
            [
                _node(root, None, 1, "1"),
                _node(deliverable, root, 2, "1.1"),
                _node(
                    sign_in,
                    deliverable,
                    3,
                    "1.1.1",
                    name="Staff sign-in",
                    description="Single sign-on (SSO) through Okta",
                    duration_days=5,
                    owner_employee_id=mark,
                ),
                _node(api, deliverable, 3, "1.1.2", name="Build API", duration_days=10),
            ]
        ),
        Rows([]),  # no skills tagged on step 4
        Rows([{"skill_id": okta, "name": "Okta SSO", "required": True}]),
        Rows(
            [
                {
                    "id": mark,
                    "full_name": "Mark Chen",
                    "hours_per_week": Decimal("40.0"),
                    "hourly_rate_cents": 9_000,
                    "is_contractor": False,
                }
            ]
        ),
        Rows([]),  # Mark has no Okta skill
        Rows([]),  # no dependencies
        Rows([constraint_set]),
    )
    inp = await OptimizationRepository(conn).load_input(project_ctx(), date(2026, 10, 1))  # type: ignore[arg-type]
    assert inp.tasks[0].skills == (SkillRequirement(str(okta), "Okta SSO", 3),)
    assert (inp.constraints.allow_contractors, inp.constraints.contractor_rate_cents) == (
        True,
        18_500,
    )
    facts = facts_from_input(inp, skills=[SkillFact("Okta SSO", True, False)])
    assert facts.unlinked_skills == []

    outcome = solve(inp, FAST)
    assert outcome.plan is not None
    assert outcome.plan.tasks[str(sign_in)].resource_id == f"contractor:{okta}"
    report = build_report(inp, outcome)
    assert report.result is not None
    assert report.result["summary"]["specialists"] == [
        {
            "name": "Contractor: Okta SSO specialist",
            "hours": 40,
            "rateCents": 18_500,
            "costCents": 40 * 18_500,
        }
    ]
    assert report.expected_cost_cents == 40 * 18_500 + 80 * 9_000


async def test_set_skills_reuses_org_skills_and_adds_new_ones_at_level_3() -> None:
    org, node, okta = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    known = Conn(Rows([{"id": okta, "name": "Okta SSO"}]))
    await WbsRepository(known).set_skills(org, node, ["okta sso"])  # type: ignore[arg-type]
    select, delete, insert = known.sql
    assert "FROM public.skills" in select
    assert "DELETE FROM public.task_skill_requirements" in delete
    assert "skill_id NOT IN" in delete  # kept skills keep their level
    assert "INSERT INTO public.task_skill_requirements" in insert
    assert "ON CONFLICT" in insert
    assert known.params[2]["min_proficiency_m0"] == 3

    new = Conn(Rows([]), Rows([]), Rows([{"id": okta, "name": "Okta SSO"}]))
    await WbsRepository(new).set_skills(org, node, ["Okta SSO"])  # type: ignore[arg-type]
    assert "INSERT INTO public.skills" in new.sql[1]
    assert len(new.sql) == 5

    cleared = Conn()
    await WbsRepository(cleared).set_skills(org, node, [])  # type: ignore[arg-type]
    assert len(cleared.sql) == 1
    assert "NOT IN" not in cleared.sql[0]
