"""What the optimizer's confirmation sheet needs beyond the planning input.

Runs under the caller's RLS session. Reads the answers to the step-5
questions, which AI estimates a person confirmed, the dependency types as
the PM entered them (before summary nodes are expanded), the project's
skill list and whether the budget is stated in the notes, and combines them
with the loaded input via `facts_from_input`. Rendering is pure:
`app/optimization/input_sheet.py`.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.common import ItemOrigin, ScopeStatus
from app.optimization.inputs import InputFacts, SkillFact, facts_from_input, quote_states_amount
from app.optimization.model import PlanningInput
from app.repositories.planning_input import ProjectContext, read_constraint_set
from app.repositories.tables import (
    dependencies,
    project_skills,
    project_team_members,
    scope_items,
    skills,
    wbs_nodes,
)


class InputFactsReader:
    """Queries behind the confirmation sheet."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def facts(self, project: ProjectContext, inp: PlanningInput) -> InputFacts:
        """What the confirmation sheet needs beyond `inp`: answers, confirmations, sources."""
        constraint_set = await read_constraint_set(self._conn, project.id)
        confirmed_ai, unconfirmed = await self._estimate_counts([task.id for task in inp.tasks])
        dependency_types = {
            str(row.type): int(row.total)
            for row in (
                await self._conn.execute(
                    sa.select(dependencies.c.type, sa.func.count().label("total"))
                    .where(dependencies.c.project_id == project.id)
                    .group_by(dependencies.c.type)
                )
            ).all()
        }
        team_from_project = bool(
            (
                await self._conn.execute(
                    sa.select(sa.func.count())
                    .select_from(project_team_members)
                    .where(project_team_members.c.project_id == project.id)
                )
            ).scalar()
        )
        project_skill_rows = (
            await self._conn.execute(
                sa.select(project_skills.c.skill_id, skills.c.name, project_skills.c.required)
                .select_from(project_skills.join(skills, skills.c.id == project_skills.c.skill_id))
                .where(project_skills.c.project_id == project.id)
                .order_by(sa.desc(project_skills.c.required), skills.c.name)
            )
        ).all()
        held = {skill_id for employee in inp.employees for skill_id in employee.skills}
        budget_from_constraints = (
            constraint_set is not None and constraint_set.budget_total_cents is not None
        )
        budget = inp.constraints.budget_total_cents
        quote = None
        if budget is not None and not budget_from_constraints:
            quote = await self._budget_quote(project.id, budget)
        answers: dict[str, Any] = {}
        if constraint_set is not None:
            answers = {
                "deadline_answer": constraint_set.deadline_answer,
                "specialist_answer": constraint_set.specialist_answer,
                "specialist_rate_confirmed": bool(constraint_set.specialist_rate_confirmed),
                "contingency_pct": float(constraint_set.contingency_pct),
            }
        return facts_from_input(
            inp,
            confirmed_ai_estimates=confirmed_ai,
            unconfirmed_estimates=unconfirmed,
            dependency_types=dependency_types,
            budget_quote=quote,
            budget_from_constraints=budget_from_constraints,
            team_from_project=team_from_project,
            skills=[
                SkillFact(str(row.name), bool(row.required), str(row.skill_id) in held)
                for row in project_skill_rows
            ],
            **answers,
        )

    async def _estimate_counts(self, task_ids: list[str]) -> tuple[int, int]:
        """(AI estimates a person confirmed, AI estimates nobody confirmed yet)."""
        if not task_ids:
            return 0, 0
        rows = (
            await self._conn.execute(
                sa.select(wbs_nodes.c.estimate_confirmed_at).where(
                    wbs_nodes.c.id.in_([uuid.UUID(task_id) for task_id in task_ids]),
                    wbs_nodes.c.origin == ItemOrigin.AI,
                )
            )
        ).all()
        unconfirmed = sum(1 for row in rows if row.estimate_confirmed_at is None)
        return len(rows) - unconfirmed, unconfirmed

    async def _budget_quote(self, project_id: uuid.UUID, budget_cents: int) -> str | None:
        """A notes quote that states this budget (shown as the source), if any."""
        rows = (
            await self._conn.execute(
                sa.select(scope_items.c.source_quote)
                .where(
                    scope_items.c.project_id == project_id,
                    scope_items.c.status != ScopeStatus.REMOVED,
                    scope_items.c.source_quote.ilike("%budget%"),
                )
                .order_by(scope_items.c.sort_order)
            )
        ).all()
        return next(
            (
                str(row.source_quote)
                for row in rows
                if quote_states_amount(str(row.source_quote), budget_cents)
            ),
            None,
        )
