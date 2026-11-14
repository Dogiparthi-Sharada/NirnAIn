"""Facts for the per-project dashboard, read under the caller's RLS session."""

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
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def facts(self, project: Project, user_id: uuid.UUID) -> OverviewFacts:
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
        c = constraint_sets
        stmt = sa.select(
            c.c.budget_mode,
            c.c.capacity_mode,
            c.c.skill_mode,
            c.c.deadline_answer,
            c.c.specialist_answer,
        ).where(c.c.project_id == project_id)
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        m = row._mapping
        return PolicyFacts(
            budget_mode=ConstraintMode(m["budget_mode"]),
            capacity_mode=ConstraintMode(m["capacity_mode"]),
            skill_mode=ConstraintMode(m["skill_mode"]),
            deadline_answer=m["deadline_answer"],
            specialist_answer=m["specialist_answer"],
        )

    async def _progress(self, project_id: uuid.UUID) -> ProgressFacts:
        s = scope_items
        scope = (
            await self._conn.execute(
                sa.select(
                    sa.func.count().filter(s.c.status != ScopeStatus.REMOVED),
                    sa.func.count().filter(s.c.status == ScopeStatus.PENDING),
                ).where(s.c.project_id == project_id)
            )
        ).one()

        n, child = wbs_nodes, wbs_nodes.alias("child")
        is_leaf = ~sa.exists().where(child.c.parent_id == n.c.id)
        wbs = (
            await self._conn.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.count().filter(
                        n.c.origin == ItemOrigin.AI,
                        n.c.level > 1,
                        is_leaf,
                        n.c.estimate_confirmed_at.is_(None),
                    ),
                ).where(n.c.project_id == project_id)
            )
        ).one()

        r = optimization_runs
        succeeded = (
            await self._conn.execute(
                sa.select(sa.exists().where(r.c.project_id == project_id, r.c.status.in_(_SUCCESS)))
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
        r = optimization_runs
        stmt = (
            sa.select(
                r.c.id,
                r.c.version,
                r.c.applied_at,
                r.c.expected_cost_cents,
                r.c.completion_date,
                r.c.result,
            )
            .where(r.c.project_id == project_id, r.c.version.is_not(None))
            .order_by(r.c.version.desc())
            .limit(1)
        )
        row = (await self._conn.execute(stmt)).first()
        if row is None:
            return None
        m = row._mapping
        result: Any = m["result"]
        return AppliedRunFacts(
            run_id=m["id"],
            version=int(m["version"]),
            applied_at=m["applied_at"],
            expected_cost_cents=m["expected_cost_cents"],
            completion_date=m["completion_date"],
            result=result if isinstance(result, dict) else None,
        )

    async def _scope_risks(self, project_id: uuid.UUID) -> list[ScopeRisk]:
        s = scope_items
        stmt = (
            sa.select(s.c.text, s.c.status)
            .where(
                s.c.project_id == project_id,
                s.c.category == ScopeCategory.RISK,
                s.c.status.in_([ScopeStatus.ACCEPTED, ScopeStatus.PENDING]),
            )
            .order_by(s.c.sort_order, s.c.created_at)
        )
        rows = (await self._conn.execute(stmt)).all()
        return [
            ScopeRisk(text=r._mapping["text"], status=ScopeStatus(r._mapping["status"]))
            for r in rows
        ]

    def _notification_filter(
        self, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> sa.ColumnElement[bool]:
        n = notifications
        # RLS already limits rows to the caller; the explicit user filter uses the index.
        return sa.and_(n.c.user_id == user_id, n.c.project_id == project_id)

    async def _notifications(self, project_id: uuid.UUID, user_id: uuid.UUID) -> list[Notification]:
        n = notifications
        stmt = (
            sa.select(
                n.c.id,
                n.c.org_id,
                n.c.project_id,
                n.c.level,
                n.c.message,
                n.c.read_at,
                n.c.created_at,
            )
            .where(self._notification_filter(project_id, user_id))
            .order_by(n.c.created_at.desc(), n.c.id.desc())
            .limit(MAX_NOTIFICATIONS)
        )
        rows = (await self._conn.execute(stmt)).all()
        return [Notification.model_validate(dict(r._mapping)) for r in rows]

    async def _notification_total(self, project_id: uuid.UUID, user_id: uuid.UUID) -> int:
        stmt = sa.select(sa.func.count()).where(self._notification_filter(project_id, user_id))
        return int((await self._conn.execute(stmt)).scalar() or 0)

    async def _validation_issues(self, project_id: uuid.UUID) -> list[str]:
        j = ai_jobs
        stmt = (
            sa.select(j.c.result)
            .where(
                j.c.project_id == project_id,
                j.c.kind == AiJobKind.WBS_VALIDATE,
                j.c.status == JobStatus.SUCCEEDED,
            )
            .order_by(j.c.created_at.desc())
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
            r = optimization_runs
            run_id = (
                await self._conn.execute(
                    sa.select(r.c.id)
                    .where(r.c.project_id == project_id, r.c.status.in_(_SUCCESS))
                    .order_by(r.c.created_at.desc())
                    .limit(1)
                )
            ).scalar()
        if run_id is None:
            return []
        rec = recommendations
        stmt = (
            sa.select(rec.c.after_value, rec.c.reason)
            .where(rec.c.run_id == run_id)
            .order_by(rec.c.sort_order)
            .limit(MAX_RECOMMENDATIONS)
        )
        rows = (await self._conn.execute(stmt)).all()
        out = []
        for row in rows:
            after, reason = row._mapping["after_value"], row._mapping["reason"]
            out.append(f"{after}: {reason}" if after else str(reason))
        return out
