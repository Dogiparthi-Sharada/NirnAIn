"""Facts for the per-project dashboard, read under the caller's RLS session.

Purpose: Gather the plain facts the overview is derived from (policy answers, workflow progress,
milestones, the plan in force, risks, notifications, AI findings) without interpreting them.
How it fits: Used by app/api/v1/overview.py; fills app/domain/overview_facts.py, which
app/domain/overview.py turns into the response; layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.common import (
    AiJobKind,
    ConstraintMode,
    ItemOrigin,
    JobStatus,
    ScopeCategory,
    ScopeStatus,
    SolverStatus,
)
from app.domain.notifications import Notification
from app.domain.overview_facts import (
    AppliedRunFacts,
    OverviewFacts,
    PolicyFacts,
    ProgressFacts,
    ScopeRisk,
)
from app.domain.overview_milestones import MilestoneFact
from app.domain.projects import Project
from app.repositories.tables import (
    ai_jobs,
    constraint_sets,
    milestones,
    notifications,
    optimization_runs,
    recommendations,
    scope_items,
    wbs_nodes,
)

MAX_NOTIFICATIONS = 10
MAX_ISSUES = 3
MAX_RECOMMENDATIONS = 3
_SUCCESS = (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)


class OverviewRepository:
    """Reads everything the per-project dashboard needs, one small query per fact."""

    def __init__(self, conn: AsyncConnection) -> None:
        """Use the caller's RLS-scoped connection."""
        self._conn = conn

    async def facts(self, project: Project, user_id: uuid.UUID) -> OverviewFacts:
        """Gather all overview facts for the project as the given user sees them."""
        applied = await self._applied_run(project.id)
        return OverviewFacts(
            project=project,
            policy=await self._policy(project.id),
            progress=await self._progress(project.id),
            milestones=await self._milestones(project.id),
            applied_run=applied,
            scope_risks=await self._scope_risks(project.id),
            notifications=await self._notifications(project.id, user_id),
            notification_total=await self._notification_total(project.id, user_id),
            validation_issues=await self._validation_issues(project.id),
            recommendation_reasons=await self._recommendation_reasons(
                project.id, applied.run_id if applied else None
            ),
        )

    async def _policy(self, project_id: uuid.UUID) -> PolicyFacts | None:
        """Return the project's constraint policies and answers, or None before step 5 is saved."""
        stmt = sa.select(
            constraint_sets.c.budget_mode,
            constraint_sets.c.capacity_mode,
            constraint_sets.c.skill_mode,
            constraint_sets.c.deadline_answer,
            constraint_sets.c.specialist_answer,
        ).where(constraint_sets.c.project_id == project_id)
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        mapping = row._mapping
        return PolicyFacts(
            budget_mode=ConstraintMode(mapping["budget_mode"]),
            capacity_mode=ConstraintMode(mapping["capacity_mode"]),
            skill_mode=ConstraintMode(mapping["skill_mode"]),
            deadline_answer=mapping["deadline_answer"],
            specialist_answer=mapping["specialist_answer"],
        )

    async def _progress(self, project_id: uuid.UUID) -> ProgressFacts:
        """Count scope items, WBS nodes and unconfirmed AI estimates; check for a successful run.

        Unconfirmed estimates count AI-made leaf nodes below the root whose estimate nobody
        confirmed.
        """
        scope = (
            await self._conn.execute(
                sa.select(
                    sa.func.count().filter(scope_items.c.status != ScopeStatus.REMOVED),
                    sa.func.count().filter(scope_items.c.status == ScopeStatus.PENDING),
                ).where(scope_items.c.project_id == project_id)
            )
        ).one()

        child = wbs_nodes.alias("child")
        is_leaf = ~sa.exists().where(child.c.parent_id == wbs_nodes.c.id)
        wbs = (
            await self._conn.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.count().filter(
                        wbs_nodes.c.origin == ItemOrigin.AI,
                        wbs_nodes.c.level > 1,
                        is_leaf,
                        wbs_nodes.c.estimate_confirmed_at.is_(None),
                    ),
                ).where(wbs_nodes.c.project_id == project_id)
            )
        ).one()

        runs = optimization_runs
        succeeded = (
            await self._conn.execute(
                sa.select(
                    sa.exists().where(runs.c.project_id == project_id, runs.c.status.in_(_SUCCESS))
                )
            )
        ).scalar()
        return ProgressFacts(
            scope_items=int(scope[0] or 0),
            pending_scope_items=int(scope[1] or 0),
            wbs_nodes=int(wbs[0] or 0),
            unconfirmed_ai_estimates=int(wbs[1] or 0),
            has_successful_run=bool(succeeded),
        )

    async def _milestones(self, project_id: uuid.UUID) -> list[MilestoneFact]:
        """The project's milestones, each with its WBS node and everything under it."""
        stmt = (
            sa.select(milestones.c.name, milestones.c.due_date, milestones.c.wbs_node_id)
            .where(milestones.c.project_id == project_id)
            .order_by(milestones.c.due_date, milestones.c.name)
        )
        rows = (await self._conn.execute(stmt)).all()
        if not any(row.wbs_node_id is not None for row in rows):
            return [MilestoneFact(name=row.name, due_date=row.due_date) for row in rows]
        node_rows = (
            await self._conn.execute(
                sa.select(wbs_nodes.c.id, wbs_nodes.c.parent_id).where(
                    wbs_nodes.c.project_id == project_id
                )
            )
        ).all()
        children: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for node in node_rows:
            if node.parent_id is not None:
                children[node.parent_id].append(node.id)

        def subtree(node_id: uuid.UUID) -> frozenset[str]:
            """Return the node and every node under it, as id strings."""
            found: set[str] = set()
            pending = [node_id]
            while pending:
                current = pending.pop()
                found.add(str(current))
                pending.extend(children.get(current, []))
            return frozenset(found)

        return [
            MilestoneFact(
                name=row.name,
                due_date=row.due_date,
                node_ids=subtree(row.wbs_node_id) if row.wbs_node_id is not None else frozenset(),
            )
            for row in rows
        ]

    async def _applied_run(self, project_id: uuid.UUID) -> AppliedRunFacts | None:
        """Return the plan in force (the highest applied version), or None."""
        runs = optimization_runs
        stmt = (
            sa.select(
                runs.c.id,
                runs.c.version,
                runs.c.applied_at,
                runs.c.expected_cost_cents,
                runs.c.completion_date,
                runs.c.result,
            )
            .where(runs.c.project_id == project_id, runs.c.version.is_not(None))
            .order_by(runs.c.version.desc())
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        mapping = row._mapping
        result: Any = mapping["result"]
        return AppliedRunFacts(
            run_id=mapping["id"],
            version=int(mapping["version"]),
            applied_at=mapping["applied_at"],
            expected_cost_cents=mapping["expected_cost_cents"],
            completion_date=mapping["completion_date"],
            result=result if isinstance(result, dict) else None,
        )

    async def _scope_risks(self, project_id: uuid.UUID) -> list[ScopeRisk]:
        """Return the risk scope items that are accepted or still pending review."""
        stmt = (
            sa.select(scope_items.c.text, scope_items.c.status)
            .where(
                scope_items.c.project_id == project_id,
                scope_items.c.category == ScopeCategory.RISK,
                scope_items.c.status.in_([ScopeStatus.ACCEPTED, ScopeStatus.PENDING]),
            )
            .order_by(scope_items.c.sort_order, scope_items.c.created_at)
        )
        rows = (await self._conn.execute(stmt)).all()
        return [
            ScopeRisk(text=row._mapping["text"], status=ScopeStatus(row._mapping["status"]))
            for row in rows
        ]

    def _notification_filter(
        self, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> sa.ColumnElement[bool]:
        """Return the filter for the user's notifications about this project."""
        # RLS already limits rows to the caller; the explicit user filter uses the index.
        return sa.and_(notifications.c.user_id == user_id, notifications.c.project_id == project_id)

    async def _notifications(self, project_id: uuid.UUID, user_id: uuid.UUID) -> list[Notification]:
        """Return the user's latest notifications about this project."""
        stmt = (
            sa.select(
                notifications.c.id,
                notifications.c.org_id,
                notifications.c.project_id,
                notifications.c.level,
                notifications.c.message,
                notifications.c.read_at,
                notifications.c.created_at,
            )
            .where(self._notification_filter(project_id, user_id))
            .order_by(notifications.c.created_at.desc(), notifications.c.id.desc())
            .limit(MAX_NOTIFICATIONS)
        )
        rows = (await self._conn.execute(stmt)).all()
        return [Notification.model_validate(dict(row._mapping)) for row in rows]

    async def _notification_total(self, project_id: uuid.UUID, user_id: uuid.UUID) -> int:
        """Count all of the user's notifications about this project."""
        stmt = sa.select(sa.func.count()).where(self._notification_filter(project_id, user_id))
        return int((await self._conn.execute(stmt)).scalar() or 0)

    async def _validation_issues(self, project_id: uuid.UUID) -> list[str]:
        """Return the first issue texts of the latest successful AI WBS validation."""
        stmt = (
            sa.select(ai_jobs.c.result)
            .where(
                ai_jobs.c.project_id == project_id,
                ai_jobs.c.kind == AiJobKind.WBS_VALIDATE,
                ai_jobs.c.status == JobStatus.SUCCEEDED,
            )
            .order_by(ai_jobs.c.created_at.desc())
            .limit(1)
        )
        result: Any = (await self._conn.execute(stmt)).scalar()
        issues = result.get("issues") if isinstance(result, dict) else None
        if not isinstance(issues, list):
            return []
        texts = [
            str(issue["text"]).strip()
            for issue in issues
            if isinstance(issue, dict) and str(issue.get("text") or "").strip()
        ]
        return texts[:MAX_ISSUES]

    async def _recommendation_reasons(
        self, project_id: uuid.UUID, applied_run_id: uuid.UUID | None
    ) -> list[str]:
        """Top reasons of the applied run, else of the latest successful run."""
        run_id = applied_run_id
        if run_id is None:
            runs = optimization_runs
            run_id = (
                await self._conn.execute(
                    sa.select(runs.c.id)
                    .where(runs.c.project_id == project_id, runs.c.status.in_(_SUCCESS))
                    .order_by(runs.c.created_at.desc())
                    .limit(1)
                )
            ).scalar()
        if run_id is None:
            return []
        stmt = (
            sa.select(recommendations.c.after_value, recommendations.c.reason)
            .where(recommendations.c.run_id == run_id)
            .order_by(recommendations.c.sort_order)
            .limit(MAX_RECOMMENDATIONS)
        )
        rows = (await self._conn.execute(stmt)).all()
        out = []
        for row in rows:
            after, reason = row._mapping["after_value"], row._mapping["reason"]
            out.append(f"{after}: {reason}" if after else str(reason))
        return out
