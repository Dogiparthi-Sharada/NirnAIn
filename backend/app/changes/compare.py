"""Plan vN+1 against Plan vN: per work package bars before/after and why they moved."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, timedelta

from app.changes import parsing
from app.changes.context import NodeRow, PlanRow, RunMeta
from app.changes.team import is_addition, is_removal
from app.domain.changes import (
    Bar,
    ChangeComparison,
    ChangeImpact,
    CompareRow,
    PlanSide,
)


def _snapshot_start(meta: RunMeta) -> tuple[date, int]:
    raw = meta.snapshot.get("start_date")
    start = date.fromisoformat(raw) if isinstance(raw, str) else date.today()
    per_week = int((meta.snapshot.get("constraints") or {}).get("working_days_per_week") or 5)
    return start - timedelta(days=start.weekday()), min(max(per_week, 1), 7)


def _bars(
    meta: RunMeta, rows: Sequence[PlanRow], names: dict[uuid.UUID, str]
) -> dict[uuid.UUID, Bar]:
    """Week indexes count from the Monday of the run's planning start (as planned_assignments)."""
    monday, per_week = _snapshot_start(meta)
    out: dict[uuid.UUID, Bar] = {}
    for row in rows:
        start = monday + timedelta(days=7 * row.start_week)
        end = monday + timedelta(days=7 * row.end_week + per_week - 1)
        owner = names.get(row.employee_id) if row.employee_id is not None else row.contractor_label
        previous = out.get(row.wbs_node_id)
        if previous is None:
            out[row.wbs_node_id] = Bar(start=start, end=end, owner=owner)
        else:
            out[row.wbs_node_id] = Bar(
                start=min(previous.start, start), end=max(previous.end, end), owner=previous.owner
            )
    return out


def _side(meta: RunMeta) -> PlanSide:
    constraints = meta.snapshot.get("constraints") or {}
    finish = constraints.get("must_finish_by")
    budget = constraints.get("budget_total_cents")
    return PlanSide(
        run_id=meta.id,
        version=meta.version,
        status=meta.status,
        expected_cost_cents=meta.expected_cost_cents,
        completion_date=meta.completion_date,
        budget_cents=int(budget) if budget is not None else None,
        must_finish_by=date.fromisoformat(finish) if isinstance(finish, str) else None,
    )


def _labels(meta: RunMeta) -> dict[str, tuple[str, str]]:
    return {
        str(task.get("id")): (str(task.get("code") or ""), str(task.get("name") or ""))
        for task in meta.snapshot.get("tasks") or []
    }


def _preds(meta: RunMeta) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for dependency in meta.snapshot.get("dependencies") or []:
        out.setdefault(str(dependency.get("successor_id")), []).append(
            str(dependency.get("predecessor_id"))
        )
    return out


def _as_number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _weeks(value: object) -> str:
    return parsing.format_days(_as_number(value))


def _hours(value: object) -> str:
    return parsing.format_hours(_as_number(value))


def _change_reasons(
    impact: ChangeImpact | None,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Why per work package id, per person id who changes or leaves, and per newcomer id,
    from the change itself."""
    tasks: dict[str, str] = {}
    people: dict[str, str] = {}
    joined: dict[str, str] = {}
    if impact is None:
        return tasks, people, joined
    for change in impact.changes:
        if is_addition(change) and change.target is not None and change.member is not None:
            joined[str(change.target)] = f"{change.member.full_name} joins the team"
            continue
        targets = (
            [(change.target, change.from_, change.to)]
            if change.target
            else [(option.id, option.from_, option.to) for option in change.candidates]
        )
        for target, before, after in targets:
            if change.kind == "duration":
                tasks[str(target)] = (
                    "Done, nothing left to plan"
                    if after == 0
                    else f"Effort {_weeks(before)} → {_weeks(after)}"
                )
            elif change.kind == "capacity":
                people[str(target)] = (
                    f"{change.target_label} leaves the project"
                    if after == 0
                    else f"{change.target_label} now gives {_hours(after)} a week"
                )
            elif is_removal(change):
                people[str(target)] = f"{change.target_label} leaves the team"
    return tasks, people, joined


def build_comparison(
    base: RunMeta,
    base_rows: Sequence[PlanRow],
    proposed: RunMeta,
    proposed_rows: Sequence[PlanRow],
    nodes: Sequence[NodeRow],
    names: dict[uuid.UUID, str],
    impact: ChangeImpact | None,
) -> ChangeComparison:
    """Rows per work package of either plan, in WBS order, with shifts and a reason.

    The reason comes from the change itself when it targets the package, its owner (who
    changes hours or leaves) or its new owner (who joins the team), else "Follows
    <predecessor>" when something it waits on moved, else "Re-solved".
    """
    before = _bars(base, base_rows, names)
    after = _bars(proposed, proposed_rows, names)
    labels = {
        **{str(node.id): (node.code, node.name) for node in nodes},
        **_labels(base),
        **_labels(proposed),
    }
    by_id = {node.id: node for node in nodes}
    order = {node.id: position for position, node in enumerate(nodes)}
    preds = _preds(proposed)
    task_why, person_why, joined_why = _change_reasons(impact)
    owner_ids = {
        row.wbs_node_id: row.employee_id for row in base_rows if row.employee_id is not None
    }
    new_owner_ids = {
        row.wbs_node_id: row.employee_id for row in proposed_rows if row.employee_id is not None
    }

    ids = sorted(
        set(before) | set(after), key=lambda node_id: (order.get(node_id, len(order)), str(node_id))
    )
    shifts: dict[str, int | None] = {}
    for node_id in ids:
        before_bar, after_bar = before.get(node_id), after.get(node_id)
        shifts[str(node_id)] = (
            (after_bar.end - before_bar.end).days if after_bar and before_bar else None
        )

    rows: list[CompareRow] = []
    for node_id in ids:
        before_bar, after_bar = before.get(node_id), after.get(node_id)
        node = by_id.get(node_id)
        code, name = labels.get(str(node_id), ("", ""))
        parent = by_id.get(node.parent_id) if node and node.parent_id else None
        group = parent if parent is not None and parent.level >= 2 else node
        owner_changed = bool(after_bar and before_bar and after_bar.owner != before_bar.owner)
        moved = bool(
            after_bar
            and before_bar
            and (after_bar.start != before_bar.start or after_bar.end != before_bar.end)
        )
        why = task_why.get(str(node_id))
        if why is None:
            base_owner = owner_ids.get(node_id)
            new_owner = new_owner_ids.get(node_id)
            if base_owner is not None and str(base_owner) in person_why:
                why = person_why[str(base_owner)]
            elif new_owner is not None and str(new_owner) in joined_why:
                why = joined_why[str(new_owner)]
            elif moved:
                moved_preds = [
                    labels.get(predecessor_id, ("", ""))[0]
                    for predecessor_id in preds.get(str(node_id), [])
                    if shifts.get(predecessor_id)
                ]
                why = f"Follows {', '.join(moved_preds)}" if moved_preds else "Re-solved"
            elif owner_changed:
                why = "Owner changed in the re-plan"
        rows.append(
            CompareRow(
                wbs_node_id=node_id,
                code=code,
                name=name,
                group_code=group.code if group else None,
                group_name=group.name if group else None,
                before=before_bar,
                after=after_bar,
                shift_days=shifts[str(node_id)],
                owner_changed=owner_changed,
                why=why,
            )
        )
    return ChangeComparison(base=_side(base), proposed=_side(proposed), rows=rows)
