"""Plan export as Microsoft Project XML (MSPDI): Tasks, Resources, Assignments.

Each work package becomes a task with its planned start ("start no earlier
than", so Project keeps the optimizer's dates), duration and WBS code;
dependencies become predecessor links. People and contractors become work
resources, assigned at their planned hours per week.
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


def _sub(parent: ET.Element, tag: str, text: str | int) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = str(text)
    return el


def _start(d: date) -> str:
    return f"{d.isoformat()}T08:00:00"


def _finish(d: date) -> str:
    return f"{d.isoformat()}T17:00:00"


def _hours(hours: float) -> str:
    whole, minutes = divmod(round(hours * 60), 60)
    return f"PT{whole}H{minutes}M0S"


def _task(tasks: ET.Element, t: ExportTask, links: dict[int, list[tuple[int, str, int]]]) -> None:
    el = ET.SubElement(tasks, "Task")
    _sub(el, "UID", t.uid)
    _sub(el, "ID", t.uid)
    _sub(el, "Name", t.name)
    _sub(el, "IsNull", 0)
    _sub(el, "WBS", t.code)
    _sub(el, "OutlineNumber", t.uid)
    _sub(el, "OutlineLevel", 1)
    _sub(el, "Start", _start(t.start))
    _sub(el, "Finish", _finish(t.finish))
    _sub(el, "Duration", _hours(t.duration_days * HOURS_PER_DAY))
    _sub(el, "DurationFormat", DURATION_DAYS)
    _sub(el, "Work", _hours(t.work_hours))
    _sub(el, "Milestone", 0)
    _sub(el, "Summary", 0)
    _sub(el, "ConstraintType", START_NO_EARLIER_THAN)
    _sub(el, "ConstraintDate", _start(t.start))
    for pred_uid, link_type, lag_days in links.get(t.uid, []):
        link = ET.SubElement(el, "PredecessorLink")
        _sub(link, "PredecessorUID", pred_uid)
        _sub(link, "Type", LINK_TYPES.get(link_type, "1"))
        _sub(link, "CrossProject", 0)
        # LinkLag is in tenths of a minute.
        _sub(link, "LinkLag", lag_days * HOURS_PER_DAY * 60 * 10)
        _sub(link, "LagFormat", DURATION_DAYS)


def _summary(tasks: ET.Element, plan: PlanExport, finish: date) -> None:
    el = ET.SubElement(tasks, "Task")
    _sub(el, "UID", 0)
    _sub(el, "ID", 0)
    _sub(el, "Name", plan.project_name)
    _sub(el, "IsNull", 0)
    _sub(el, "OutlineNumber", 0)
    _sub(el, "OutlineLevel", 0)
    _sub(el, "Start", _start(plan.start_date))
    _sub(el, "Finish", _finish(finish))
    _sub(el, "Summary", 1)


def _resources(root: ET.Element, plan: PlanExport) -> dict[str, int]:
    resources = ET.SubElement(root, "Resources")
    uids: dict[str, int] = {}
    for t in plan.tasks:
        if t.resource_key is None or t.resource_key in uids:
            continue
        uid = len(uids) + 1
        uids[t.resource_key] = uid
        el = ET.SubElement(resources, "Resource")
        _sub(el, "UID", uid)
        _sub(el, "ID", uid)
        _sub(el, "Name", t.resource or "Unnamed")
        _sub(el, "Type", 1)  # work resource
        _sub(el, "IsNull", 0)
        if t.is_contractor:
            _sub(el, "Group", "Contractor")
    return uids


def _assignments(root: ET.Element, plan: PlanExport, resource_uids: dict[str, int]) -> None:
    assignments = ET.SubElement(root, "Assignments")
    full_time_week = HOURS_PER_DAY * plan.working_days_per_week
    for t in plan.tasks:
        if t.resource_key is None:
            continue
        el = ET.SubElement(assignments, "Assignment")
        _sub(el, "UID", t.uid)
        _sub(el, "TaskUID", t.uid)
        _sub(el, "ResourceUID", resource_uids[t.resource_key])
        _sub(el, "Finish", _finish(t.finish))
        _sub(el, "Start", _start(t.start))
        _sub(el, "Units", f"{t.hours_per_week / full_time_week:.2f}")
        _sub(el, "Work", _hours(t.work_hours))


def render_msproject(plan: PlanExport) -> bytes:
    finish = plan.finish_date or plan.start_date
    root = ET.Element("Project", {"xmlns": NAMESPACE})
    _sub(root, "SaveVersion", 14)
    _sub(root, "Name", f"{plan.file_stem}.xml")
    _sub(root, "Title", f"{plan.project_name} - {plan.title}")
    _sub(root, "ScheduleFromStart", 1)
    _sub(root, "StartDate", _start(plan.start_date))
    _sub(root, "FinishDate", _finish(finish))
    _sub(root, "MinutesPerDay", HOURS_PER_DAY * 60)
    _sub(root, "MinutesPerWeek", HOURS_PER_DAY * 60 * plan.working_days_per_week)
    _sub(root, "DaysPerMonth", 20)

    links: dict[int, list[tuple[int, str, int]]] = {}
    for link in plan.links:
        links.setdefault(link.successor_uid, []).append(
            (link.predecessor_uid, link.type, link.lag_days)
        )
    tasks = ET.SubElement(root, "Tasks")
    _summary(tasks, plan, finish)
    for t in plan.tasks:
        _task(tasks, t, links)
    _assignments(root, plan, _resources(root, plan))

    ET.indent(root)
    data: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return data
