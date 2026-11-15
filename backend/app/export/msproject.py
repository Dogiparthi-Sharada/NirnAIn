"""Plan export as Microsoft Project XML (MSPDI): tasks, resources and assignments.

Purpose: Let the PM open the optimized plan in MS Project with the optimizer's dates kept: each
package is a task with its planned start ("start no earlier than"), duration and WBS code,
dependencies become predecessor links, and people and contractors become work resources assigned at
their planned hours per week.
How it fits: `render_msproject` is called by app/api/v1/plan.py with app/export/model.py's
`PlanExport`; layer: export.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

from app.export.model import ExportTask, PlanExport

NAMESPACE = "http://schemas.microsoft.com/project"
HOURS_PER_DAY = 8
DURATION_DAYS = "7"  # DurationFormat / LagFormat: days
START_NO_EARLIER_THAN = "4"
# PredecessorLink/Type: 0 = FF, 1 = FS, 2 = SF, 3 = SS.
LINK_TYPES = {"FF": "0", "FS": "1", "SF": "2", "SS": "3"}


def _add_child(parent: ET.Element, tag: str, text: str | int) -> ET.Element:
    """Append a child element with text and return it."""
    element = ET.SubElement(parent, tag)
    element.text = str(text)
    return element


def _start(day: date) -> str:
    """Return a day's 8:00 start timestamp, as MS Project expects."""
    return f"{day.isoformat()}T08:00:00"


def _finish(day: date) -> str:
    """Return a day's 17:00 finish timestamp, as MS Project expects."""
    return f"{day.isoformat()}T17:00:00"


def _hours(hours: float) -> str:
    """Return hours as an ISO-8601 duration ("PT8H0M0S")."""
    whole, minutes = divmod(round(hours * 60), 60)
    return f"PT{whole}H{minutes}M0S"


def _task(
    tasks: ET.Element, task: ExportTask, links: dict[int, list[tuple[int, str, int]]]
) -> None:
    """Add one work package as a task with its dates, work and predecessor links."""
    element = ET.SubElement(tasks, "Task")
    _add_child(element, "UID", task.uid)
    _add_child(element, "ID", task.uid)
    _add_child(element, "Name", task.name)
    _add_child(element, "IsNull", 0)
    _add_child(element, "WBS", task.code)
    _add_child(element, "OutlineNumber", task.uid)
    _add_child(element, "OutlineLevel", 1)
    _add_child(element, "Start", _start(task.start))
    _add_child(element, "Finish", _finish(task.finish))
    _add_child(element, "Duration", _hours(task.duration_days * HOURS_PER_DAY))
    _add_child(element, "DurationFormat", DURATION_DAYS)
    _add_child(element, "Work", _hours(task.work_hours))
    _add_child(element, "Milestone", 0)
    _add_child(element, "Summary", 0)
    _add_child(element, "ConstraintType", START_NO_EARLIER_THAN)
    _add_child(element, "ConstraintDate", _start(task.start))
    for predecessor_uid, link_type, lag_days in links.get(task.uid, []):
        link = ET.SubElement(element, "PredecessorLink")
        _add_child(link, "PredecessorUID", predecessor_uid)
        _add_child(link, "Type", LINK_TYPES.get(link_type, "1"))
        _add_child(link, "CrossProject", 0)
        # LinkLag is in tenths of a minute.
        _add_child(link, "LinkLag", lag_days * HOURS_PER_DAY * 60 * 10)
        _add_child(link, "LagFormat", DURATION_DAYS)


def _summary(tasks: ET.Element, plan: PlanExport, finish: date) -> None:
    """Add the project summary task (UID 0) spanning the whole plan."""
    element = ET.SubElement(tasks, "Task")
    _add_child(element, "UID", 0)
    _add_child(element, "ID", 0)
    _add_child(element, "Name", plan.project_name)
    _add_child(element, "IsNull", 0)
    _add_child(element, "OutlineNumber", 0)
    _add_child(element, "OutlineLevel", 0)
    _add_child(element, "Start", _start(plan.start_date))
    _add_child(element, "Finish", _finish(finish))
    _add_child(element, "Summary", 1)


def _resources(root: ET.Element, plan: PlanExport) -> dict[str, int]:
    """Add one resource per person or contractor profile; return their uids by resource key."""
    resources = ET.SubElement(root, "Resources")
    resource_uids: dict[str, int] = {}
    for task in plan.tasks:
        if task.resource_key is None or task.resource_key in resource_uids:
            continue
        uid = len(resource_uids) + 1
        resource_uids[task.resource_key] = uid
        element = ET.SubElement(resources, "Resource")
        _add_child(element, "UID", uid)
        _add_child(element, "ID", uid)
        _add_child(element, "Name", task.resource or "Unnamed")
        _add_child(element, "Type", 1)  # work resource
        _add_child(element, "IsNull", 0)
        if task.is_contractor:
            _add_child(element, "Group", "Contractor")
    return resource_uids


def _assignments(root: ET.Element, plan: PlanExport, resource_uids: dict[str, int]) -> None:
    """Add each task's assignment at the person's share of a full-time week."""
    assignments = ET.SubElement(root, "Assignments")
    full_time_week = HOURS_PER_DAY * plan.working_days_per_week
    for task in plan.tasks:
        if task.resource_key is None:
            continue
        element = ET.SubElement(assignments, "Assignment")
        _add_child(element, "UID", task.uid)
        _add_child(element, "TaskUID", task.uid)
        _add_child(element, "ResourceUID", resource_uids[task.resource_key])
        _add_child(element, "Finish", _finish(task.finish))
        _add_child(element, "Start", _start(task.start))
        _add_child(element, "Units", f"{task.hours_per_week / full_time_week:.2f}")
        _add_child(element, "Work", _hours(task.work_hours))


def render_msproject(plan: PlanExport) -> bytes:
    """Return the plan as MSPDI XML bytes."""
    finish = plan.finish_date or plan.start_date
    root = ET.Element("Project", {"xmlns": NAMESPACE})
    _add_child(root, "SaveVersion", 14)
    _add_child(root, "Name", f"{plan.file_stem}.xml")
    _add_child(root, "Title", f"{plan.project_name} - {plan.title}")
    _add_child(root, "ScheduleFromStart", 1)
    _add_child(root, "StartDate", _start(plan.start_date))
    _add_child(root, "FinishDate", _finish(finish))
    _add_child(root, "MinutesPerDay", HOURS_PER_DAY * 60)
    _add_child(root, "MinutesPerWeek", HOURS_PER_DAY * 60 * plan.working_days_per_week)
    _add_child(root, "DaysPerMonth", 20)

    links: dict[int, list[tuple[int, str, int]]] = {}
    for link in plan.links:
        links.setdefault(link.successor_uid, []).append(
            (link.predecessor_uid, link.type, link.lag_days)
        )
    tasks = ET.SubElement(root, "Tasks")
    _summary(tasks, plan, finish)
    for task in plan.tasks:
        _task(tasks, task, links)
    _assignments(root, plan, _resources(root, plan))

    ET.indent(root)
    data: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return data
