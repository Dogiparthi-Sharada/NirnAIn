"""The data both export formats render, derived from a run and its assignments.

`planned_assignments` stores 0-based calendar weeks counted from the Monday of
the week containing the run's planning start (see
`app/optimization/calendar.py:week_index`). Exports show dates: a task starts
on the first working day of its start week (not before the planning start)
(moved later within that week to follow its FS/SS predecessors), runs for
its planned duration in working days, and finishes inside its end week and
not after the run's completion date.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.optimization.calendar import first_workday, workday_date
from app.optimization.formatting import weeks_label

DEFAULT_WORKING_DAYS = 5


@dataclass(frozen=True, slots=True)
class RunHeader:
    """The run and project fields an export needs (read under the caller's RLS)."""

    id: uuid.UUID
    project_name: str
    status: str
    solver: str | None
    version: int | None
    applied_at: datetime | None
    completion_date: date | None
    expected_cost_cents: int | None
    result: Mapping[str, Any] | None
    snapshot: Mapping[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlannedRow:
    """One planned assignment joined with its WBS node and employee."""

    wbs_node_id: uuid.UUID
    code: str
    name: str
    duration_days: int | None
    employee_id: uuid.UUID | None
    employee_name: str | None
    contractor_label: str | None
    start_week: int
    end_week: int
    hours_per_week: float


@dataclass(frozen=True, slots=True)
class ExportTask:
    uid: int
    wbs_node_id: str
    code: str
    name: str
    resource: str | None
    resource_key: str | None  # employee id, or contractor:<label>
    is_contractor: bool
    start: date
    finish: date
    duration_days: int
    work_hours: float
    hours_per_week: float
    weeks: str


@dataclass(frozen=True, slots=True)
class ExportLink:
    predecessor_uid: int
    successor_uid: int
    type: str  # FS | SS | FF | SF
    lag_days: int


@dataclass(frozen=True, slots=True)
class PlanExport:
    project_name: str
    title: str
    file_stem: str
    status: str
    solver: str | None
    applied_at: datetime | None
    start_date: date
    finish_date: date | None
    working_days_per_week: int
    expected_cost_cents: int | None
    kpis: list[dict[str, str]]
    reassignments: list[dict[str, str]]
    schedule_changes: list[dict[str, str]]
    tasks: list[ExportTask]
    links: list[ExportLink]


def file_stem(project_name: str, version: int | None, run_id: uuid.UUID) -> str:
    """`Acme-CRM-plan-v2` (ASCII only, safe in a Content-Disposition header)."""
    ascii_name = unicodedata.normalize("NFKD", project_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-")[:60].strip("-") or "project"
    return f"{slug}-plan-v{version}" if version is not None else f"{slug}-plan-{str(run_id)[:8]}"


def _snapshot_start(snapshot: Mapping[str, Any], fallback: date) -> date:
    try:
        return date.fromisoformat(str(snapshot["start_date"]))
    except (KeyError, TypeError, ValueError):
        return fallback


def _working_days(snapshot: Mapping[str, Any]) -> int:
    constraints = snapshot.get("constraints")
    value = constraints.get("working_days_per_week") if isinstance(constraints, dict) else None
    return value if isinstance(value, int) and 1 <= value <= 7 else DEFAULT_WORKING_DAYS


def _snapshot_durations(snapshot: Mapping[str, Any]) -> dict[str, int]:
    tasks = snapshot.get("tasks")
    durations: dict[str, int] = {}
    for task in tasks if isinstance(tasks, list) else []:
        if isinstance(task, dict) and isinstance(task.get("duration_days"), int):
            durations[str(task.get("id"))] = task["duration_days"]
    return durations


def _rows_of(result: Mapping[str, Any] | None, key: str) -> list[dict[str, str]]:
    rows = result.get(key) if result else None
    return [
        {field: str(value) for field, value in row.items() if value is not None}
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
    ]


def _resource_key(row: PlannedRow) -> str | None:
    if row.employee_id is not None:
        return str(row.employee_id)
    return f"contractor:{row.contractor_label}" if row.contractor_label else None


@dataclass(frozen=True, slots=True)
class _Window:
    """Where a task may sit: its planned start week and end week, in dates."""

    earliest_start: date
    latest_start: date
    end_monday: date
    end_last: date
    duration_days: int

    def finish(self, start: date, per_week: int, completion: date | None) -> date:
        finish = workday_date(start, max(self.duration_days, 1) - 1, per_week)
        finish = min(max(finish, self.end_monday), self.end_last)
        if completion is not None and start <= completion < finish:
            finish = completion
        return max(finish, start)


def _window(row: PlannedRow, duration_days: int, planning_start: date, per_week: int) -> _Window:
    monday = planning_start - timedelta(days=planning_start.weekday())
    start_monday = monday + timedelta(weeks=row.start_week)
    end_monday = monday + timedelta(weeks=max(row.end_week, row.start_week))
    earliest = max(first_workday(planning_start, per_week), start_monday)
    return _Window(
        earliest_start=earliest,
        latest_start=max(start_monday + timedelta(days=per_week - 1), earliest),
        end_monday=end_monday,
        end_last=end_monday + timedelta(days=per_week - 1),
        duration_days=duration_days,
    )


def _links(snapshot: Mapping[str, Any], task_uids: Mapping[str, int]) -> list[ExportLink]:
    links: list[ExportLink] = []
    dependencies = snapshot.get("dependencies")
    for dependency in dependencies if isinstance(dependencies, list) else []:
        if not isinstance(dependency, dict):
            continue
        predecessor_uid = task_uids.get(str(dependency.get("predecessor_id")))
        successor_uid = task_uids.get(str(dependency.get("successor_id")))
        if predecessor_uid is not None and successor_uid is not None:
            lag = dependency.get("lag_days")
            lag_days = lag if isinstance(lag, int) else 0
            link_type = str(dependency.get("type") or "FS")
            links.append(ExportLink(predecessor_uid, successor_uid, link_type, lag_days))
    return links


def _schedule(
    windows: list[_Window], links: list[ExportLink], per_week: int, completion: date | None
) -> list[tuple[date, date]]:
    """Dates per task: as early as its start week allows, after its FS/SS predecessors."""
    starts = [window.earliest_start for window in windows]
    finishes = [
        window.finish(start, per_week, completion)
        for window, start in zip(windows, starts, strict=True)
    ]
    for _ in range(len(windows)):
        moved = False
        for link in links:
            predecessor = link.predecessor_uid - 1
            successor = link.successor_uid - 1
            if link.type == "FS":
                bound = workday_date(finishes[predecessor], 1 + link.lag_days, per_week)
            elif link.type == "SS":
                bound = workday_date(starts[predecessor], link.lag_days, per_week)
            else:
                continue
            new_start = min(bound, windows[successor].latest_start)
            if new_start > starts[successor]:
                starts[successor] = new_start
                finishes[successor] = windows[successor].finish(new_start, per_week, completion)
                moved = True
        if not moved:
            break
    return list(zip(starts, finishes, strict=True))


def build_export(header: RunHeader, rows: Sequence[PlannedRow]) -> PlanExport:
    snapshot = header.snapshot
    planning_start = _snapshot_start(snapshot, header.created_at.date())
    per_week = _working_days(snapshot)
    durations = _snapshot_durations(snapshot)

    windows: list[_Window] = []
    task_uids: dict[str, int] = {}
    for task_uid, row in enumerate(rows, start=1):
        weeks = max(row.end_week - row.start_week + 1, 1)
        duration = durations.get(str(row.wbs_node_id)) or row.duration_days or weeks * per_week
        windows.append(_window(row, duration, planning_start, per_week))
        task_uids[str(row.wbs_node_id)] = task_uid
    links = _links(snapshot, task_uids)
    dates = _schedule(windows, links, per_week, header.completion_date)

    tasks = [
        ExportTask(
            uid=task_uid,
            wbs_node_id=str(row.wbs_node_id),
            code=row.code,
            name=row.name,
            resource=row.employee_name or row.contractor_label,
            resource_key=_resource_key(row),
            is_contractor=row.employee_id is None and row.contractor_label is not None,
            start=start,
            finish=finish,
            duration_days=window.duration_days,
            work_hours=round(row.hours_per_week * window.duration_days / per_week, 1),
            hours_per_week=row.hours_per_week,
            weeks=weeks_label(row.start_week, row.end_week),
        )
        for task_uid, (row, window, (start, finish)) in enumerate(
            zip(rows, windows, dates, strict=True), start=1
        )
    ]

    finish_date = header.completion_date or max((task.finish for task in tasks), default=None)
    return PlanExport(
        project_name=header.project_name,
        title=(
            f"Plan v{header.version}"
            if header.version is not None
            else f"Optimization run {str(header.id)[:8]}"
        ),
        file_stem=file_stem(header.project_name, header.version, header.id),
        status=header.status,
        solver=header.solver,
        applied_at=header.applied_at,
        start_date=first_workday(planning_start, per_week),
        finish_date=finish_date,
        working_days_per_week=per_week,
        expected_cost_cents=header.expected_cost_cents,
        kpis=_rows_of(header.result, "kpis"),
        reassignments=_rows_of(header.result, "reassignments"),
        schedule_changes=_rows_of(header.result, "scheduleChanges"),
        tasks=tasks,
        links=links,
    )
