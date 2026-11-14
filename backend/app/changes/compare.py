"""Plan vN+1 against Plan vN: per work package bars before/after and why they moved."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, timedelta

from app.changes import text as tx
from app.changes.context import NodeRow, PlanRow, RunMeta
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
        prev = out.get(row.wbs_node_id)
        if prev is None:
            out[row.wbs_node_id] = Bar(start=start, end=end, owner=owner)
        else:
            out[row.wbs_node_id] = Bar(
                start=min(prev.start, start), end=max(prev.end, end), owner=prev.owner
            )
    return out


def _side(meta: RunMeta) -> PlanSide:
    c = meta.snapshot.get("constraints") or {}
    finish = c.get("must_finish_by")
    budget = c.get("budget_total_cents")
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
        str(t.get("id")): (str(t.get("code") or ""), str(t.get("name") or ""))
        for t in meta.snapshot.get("tasks") or []
    }


def _preds(meta: RunMeta) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for d in meta.snapshot.get("dependencies") or []:
        out.setdefault(str(d.get("successor_id")), []).append(str(d.get("predecessor_id")))
    return out


def _n(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) else None


def _change_reasons(impact: ChangeImpact | None) -> tuple[dict[str, str], dict[str, str]]:
    """Why per work package id / per person id, from the change itself."""
    tasks: dict[str, str] = {}
    people: dict[str, str] = {}
    if impact is None:
        return tasks, people
    for c in impact.changes:
        targets = (
            [(c.target, c.from_, c.to)]
            if c.target
            else [(o.id, o.from_, o.to) for o in c.candidates]
        )
        for target, before, after in targets:
            if c.kind == "duration":
                tasks[str(target)] = (
                    "Done, nothing left to plan"
                    if after == 0
                    else f"Effort {tx.fmt_days(_n(before))} → {tx.fmt_days(_n(after))}"
                )
            elif c.kind == "capacity":
                people[str(target)] = (
                    f"{c.target_label} leaves the project"
                    if after == 0
                    else f"{c.target_label} now gives {tx.fmt_hours(_n(after))} a week"
                )
    return tasks, people


def build_comparison(
    base: RunMeta,
    base_rows: Sequence[PlanRow],
    proposed: RunMeta,
    proposed_rows: Sequence[PlanRow],
    nodes: Sequence[NodeRow],
    names: dict[uuid.UUID, str],
    impact: ChangeImpact | None,
) -> ChangeComparison:
    before = _bars(base, base_rows, names)
    after = _bars(proposed, proposed_rows, names)
    labels = {
        **{str(n.id): (n.code, n.name) for n in nodes},
        **_labels(base),
        **_labels(proposed),
    }
    by_id = {n.id: n for n in nodes}
    order = {n.id: i for i, n in enumerate(nodes)}
    preds = _preds(proposed)
    task_why, person_why = _change_reasons(impact)
    owner_ids = {
        row.wbs_node_id: row.employee_id for row in base_rows if row.employee_id is not None
    }

    ids = sorted(set(before) | set(after), key=lambda i: (order.get(i, len(order)), str(i)))
    shifts: dict[str, int | None] = {}
    for i in ids:
        b, a = before.get(i), after.get(i)
        shifts[str(i)] = (a.end - b.end).days if a and b else None

    rows: list[CompareRow] = []
    for i in ids:
        b, a = before.get(i), after.get(i)
        node = by_id.get(i)
        code, name = labels.get(str(i), ("", ""))
        parent = by_id.get(node.parent_id) if node and node.parent_id else None
        group = parent if parent is not None and parent.level >= 2 else node
        owner_changed = bool(a and b and a.owner != b.owner)
        moved = bool(a and b and (a.start != b.start or a.end != b.end))
        why = task_why.get(str(i))
        if why is None:
            base_owner = owner_ids.get(i)
            if base_owner is not None and str(base_owner) in person_why:
                why = person_why[str(base_owner)]
            elif moved:
                moved_preds = [
                    labels.get(p, ("", ""))[0] for p in preds.get(str(i), []) if shifts.get(p)
                ]
                why = f"Follows {', '.join(moved_preds)}" if moved_preds else "Re-solved"
            elif owner_changed:
                why = "Owner changed in the re-plan"
        rows.append(
            CompareRow(
                wbs_node_id=i,
                code=code,
                name=name,
                group_code=group.code if group else None,
                group_name=group.name if group else None,
                before=b,
                after=a,
                shift_days=shifts[str(i)],
                owner_changed=owner_changed,
                why=why,
            )
        )
    return ChangeComparison(base=_side(base), proposed=_side(proposed), rows=rows)
