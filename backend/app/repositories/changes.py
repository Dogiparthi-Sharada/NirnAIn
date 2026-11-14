"""Change requests of step 7 (table `change_requests`), under the caller's RLS session.

Purpose: Record a change, save its answers and decision and, on approval, apply the proposed run
(`public.apply_optimization_run`) and write the change's values into the project's inputs so every
later run uses them: constraint set, WBS durations, team and new requirements.
How it fits: The API (app/api/v1/changes.py) uses `ChangeRepository`, which extends the team writes
and plan reads; RLS on change_requests (migration 20261113000050) limits all of this to project
writers; layer: repositories.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.changes.derive import derive_overrides
from app.domain.changes import (
    ChangeImpact,
    ChangeRequest,
    ChangeSource,
    ChangeStatus,
    PlanOverrides,
)
from app.domain.common import ConstraintMode, SolverStatus
from app.repositories.access import ProjectRef
from app.repositories.change_team import ChangeTeamWrites
from app.repositories.tables import (
    change_requests,
    constraint_sets,
    optimization_runs,
    project_team_members,
    projects,
    wbs_nodes,
)

SUCCESS = (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)


def to_change(row: Any) -> ChangeRequest:
    """Row (with base_version / proposed_run_status joined in) -> API model."""
    impact = ChangeImpact.model_validate(row["impact"]) if row["impact"] else None
    answers = dict(row["answers"] or {})
    status = ChangeStatus(row["status"])
    run_status = SolverStatus(row["proposed_run_status"]) if row["proposed_run_status"] else None
    if status == ChangeStatus.REPLANNING and run_status in SUCCESS:
        status = ChangeStatus.PROPOSED
    return ChangeRequest(
        id=row["id"],
        project_id=row["project_id"],
        source=row["source"],
        input_text=row["input_text"],
        status=status,
        impact=impact,
        answers=answers,
        base_run_id=row["base_run_id"],
        base_version=row["base_version"],
        proposed_run_id=row["proposed_run_id"],
        proposed_run_status=run_status,
        error=row["error"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        overrides=derive_overrides(impact, answers) if impact else None,
    )


def _select() -> sa.Select[Any]:
    """Return the change select with base run version and proposed run status joined in."""
    base = optimization_runs.alias("base_run")
    proposed_run = optimization_runs.alias("proposed_run")
    return sa.select(
        change_requests.c.id,
        change_requests.c.project_id,
        change_requests.c.source,
        change_requests.c.input_text,
        change_requests.c.status,
        change_requests.c.impact,
        change_requests.c.answers,
        change_requests.c.base_run_id,
        change_requests.c.proposed_run_id,
        change_requests.c.error,
        change_requests.c.created_at,
        change_requests.c.decided_at,
        base.c.version.label("base_version"),
        proposed_run.c.status.label("proposed_run_status"),
    ).select_from(
        change_requests.outerjoin(base, base.c.id == change_requests.c.base_run_id).outerjoin(
            proposed_run, proposed_run.c.id == change_requests.c.proposed_run_id
        )
    )


class ChangeRepository(ChangeTeamWrites):
    """Change requests plus the plan reads (ChangePlanReads) and team writes
    (ChangeTeamWrites) they need."""

    # ------------------------------------------------------------------ change requests
    async def create(
        self,
        project_id: uuid.UUID,
        source: ChangeSource,
        input_text: str | None,
        impact: ChangeImpact,
        base_run_id: uuid.UUID,
    ) -> uuid.UUID:
        """Store an analysed change as `ready`, measured against `base_run_id`."""
        change_id = uuid.uuid4()
        await self._connection.execute(
            sa.insert(change_requests).values(
                id=change_id,
                project_id=project_id,
                source=source,
                input_text=input_text,
                status=ChangeStatus.READY,
                impact=impact.model_dump(mode="json"),
                base_run_id=base_run_id,
            )
        )
        return change_id

    async def get(self, change_id: uuid.UUID) -> ChangeRequest | None:
        """One change with its base version and proposed run status (None when not visible)."""
        row = (
            await self._connection.execute(_select().where(change_requests.c.id == change_id))
        ).first()
        return to_change(row._mapping) if row else None

    async def list(self, project_id: uuid.UUID) -> list[ChangeRequest]:
        """The project's changes, newest first (last 100)."""
        statement = (
            _select()
            .where(change_requests.c.project_id == project_id)
            .order_by(change_requests.c.created_at.desc(), change_requests.c.id.desc())
            .limit(100)
        )
        return [
            to_change(row._mapping) for row in (await self._connection.execute(statement)).all()
        ]

    async def save_answers(
        self, change_id: uuid.UUID, answers: dict[str, str], *, reset_proposal: bool
    ) -> None:
        """Replace the answers; `reset_proposal` drops a re-plan made with other answers."""
        values: dict[str, Any] = {"answers": answers}
        if reset_proposal:
            values |= {"status": ChangeStatus.READY, "proposed_run_id": None}
        await self._connection.execute(
            sa.update(change_requests).where(change_requests.c.id == change_id).values(**values)
        )

    async def decide(
        self, change_id: uuid.UUID, status: ChangeStatus, proposed_run_id: uuid.UUID | None
    ) -> None:
        """Approve or reject: sets the status and decision time (and the applied run)."""
        values: dict[str, Any] = {"status": status, "decided_at": sa.func.now()}
        if proposed_run_id is not None:
            values["proposed_run_id"] = proposed_run_id
        await self._connection.execute(
            sa.update(change_requests).where(change_requests.c.id == change_id).values(**values)
        )

    # ------------------------------------------------------------------ approve
    async def apply_run(self, run_id: uuid.UUID) -> None:
        """Plan vN+1 goes into force.

        `public.apply_optimization_run` is security definer: it checks that the caller can
        write the project, replaces the allocations and (BE1) assigns the next version.
        """
        await self._connection.execute(sa.select(sa.func.public.apply_optimization_run(run_id)))

    async def record_inputs_in_force(self, run_id: uuid.UUID, saved_inputs_hash: str) -> None:
        """Remember the inputs hash the plan came into force with.

        Approval writes the change's values into the saved inputs, so the hash the re-plan
        was solved from is no longer the hash of the inputs behind the new plan. Step 6
        compares against this one ("Inputs changed since Plan vN" must stay quiet).
        """
        runs = optimization_runs
        await self._connection.execute(
            sa.update(runs)
            .where(runs.c.id == run_id)
            .values(
                options=sa.func.coalesce(runs.c.options, sa.text("'{}'::jsonb")).op("||")(
                    sa.func.jsonb_build_object("appliedInputsHash", saved_inputs_hash)
                )
            )
        )

    async def write_overrides(self, project: ProjectRef, overrides: PlanOverrides) -> None:
        """Make the approved change the project's inputs for every later run.

        Writes the constraint set budget / must-finish-by (and the policy stated with them),
        WBS durations, team hours and the team changes, in that order.
        """
        if overrides.budget_total_cents is not None or overrides.must_finish_by is not None:
            await self._write_limits(project, overrides)
        await self._write_durations(project, overrides)
        if overrides.add_members or overrides.remove_members:
            await self.materialize_team(project)
        await self._write_hours(project, overrides)
        await self.write_team(project, overrides)

    async def _write_limits(self, project: ProjectRef, overrides: PlanOverrides) -> None:
        """Upsert the new budget / deadline (and stated policies) and mirror them on the project."""
        changes: dict[str, Any] = {"updated_at": sa.func.now()}
        if overrides.budget_total_cents is not None:
            changes["budget_total_cents"] = overrides.budget_total_cents
        if overrides.must_finish_by is not None:
            changes["must_finish_by"] = overrides.must_finish_by
        # A policy the notes stated ("must hold") is kept; the deadline answer drives
        # the deadline mode of later runs.
        policies: dict[str, Any] = {}
        if overrides.budget_mode is not None:
            policies["budget_mode"] = ConstraintMode(overrides.budget_mode)
        if overrides.deadline_mode is not None:
            policies["deadline_mode"] = ConstraintMode(overrides.deadline_mode)
            policies["deadline_answer"] = overrides.deadline_mode
        changes.update(policies)
        statement = pg_insert(constraint_sets).values(
            project_id=project.id,
            budget_total_cents=(
                overrides.budget_total_cents
                if overrides.budget_total_cents is not None
                else project.budget_cents
            ),
            start_date=project.start_date,
            must_finish_by=overrides.must_finish_by or project.target_end_date,
            **policies,
        )
        await self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[constraint_sets.c.project_id], set_=changes
            )
        )
        # Mirror on the project row so lists and the dashboard show the approved values.
        mirror: dict[str, Any] = {}
        if overrides.budget_total_cents is not None:
            mirror["budget_cents"] = overrides.budget_total_cents
        if overrides.must_finish_by is not None:
            mirror["target_end_date"] = overrides.must_finish_by
        await self._connection.execute(
            sa.update(projects).where(projects.c.id == project.id).values(**mirror)
        )

    async def _write_durations(self, project: ProjectRef, overrides: PlanOverrides) -> None:
        """Store the new work package durations as confirmed estimates."""
        # Approving the change confirms the new estimate (AI estimates count only once confirmed).
        for node_id, days in (overrides.duration_days or {}).items():
            await self._connection.execute(
                sa.update(wbs_nodes)
                .where(wbs_nodes.c.id == uuid.UUID(node_id), wbs_nodes.c.project_id == project.id)
                .values(
                    duration_days=days,
                    estimate_confirmed_at=sa.func.now(),
                    estimate_confirmed_by=sa.func.auth.uid(),
                )
            )

    async def _write_hours(self, project: ProjectRef, overrides: PlanOverrides) -> None:
        """Store the team members' new weekly hours; 0 hours takes them off the project."""
        # 0 hours = off the project; hours_per_week must stay within 1..80, so the row goes.
        # People only in the org-wide fallback pool (no project team yet) have no row to update.
        for employee_id, hours in (overrides.hours_per_week or {}).items():
            where = (
                project_team_members.c.project_id == project.id,
                project_team_members.c.employee_id == uuid.UUID(employee_id),
            )
            if hours <= 0:
                await self._connection.execute(sa.delete(project_team_members).where(*where))
            else:
                await self._connection.execute(
                    sa.update(project_team_members)
                    .where(*where)
                    .values(hours_per_week=max(hours, 1))
                )
