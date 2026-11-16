"""One start date and one deadline per project: the project's dates and step 5's stay in sync.

Purpose: A project used to hold two start dates (`projects.start_date` and the step-5
`constraint_sets.start_date`), and the checks silently used one while the PM saw the other. Every
save now writes both: step 5's start date and must-finish-by date become the project's start and
target end date, and editing the project's dates updates step 5's. A date left empty on one side
keeps the other side's, and a save that would put an end before a start is refused (422).
How it fits: Pure helpers used by app/api/v1/planning.py and app/api/v1/projects.py (the checks)
and by app/repositories/planning.py, projects.py and changes.py (the column values); layer: domain.
Created: 2026-11-15 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date
from typing import Any

# Step-5 column -> project column.
PROJECT_COLUMN = {"start_date": "start_date", "must_finish_by": "target_end_date"}
# Project column -> step-5 column.
STEP5_COLUMN = {project: step5 for step5, project in PROJECT_COLUMN.items()}


def project_columns(start_date: date | None, must_finish_by: date | None) -> dict[str, Any]:
    """The project columns a step-5 save writes: only the dates it gives (empty ones are kept)."""
    given = {"start_date": start_date, "must_finish_by": must_finish_by}
    return {PROJECT_COLUMN[name]: day for name, day in given.items() if day is not None}


def step5_columns(changes: dict[str, Any]) -> dict[str, Any]:
    """The step-5 columns a project edit writes: only the dates it sets (cleared ones are kept)."""
    return {
        STEP5_COLUMN[name]: changes[name]
        for name in STEP5_COLUMN
        if name in changes and changes[name] is not None
    }


def merged(given: date | None, kept: date | None) -> date | None:
    """The date after a save: the given one, else the one already stored."""
    return given if given is not None else kept


def inverted(start: date | None, end: date | None) -> bool:
    """Whether the end would fall before the start (what both tables' CHECKs forbid)."""
    return start is not None and end is not None and end < start


def step5_conflict(
    start_date: date | None,
    must_finish_by: date | None,
    project_start: date | None,
    project_end: date | None,
) -> str | None:
    """Why saving these step-5 dates would invert the project's dates, or None when it can't.

    A step-5 date left empty keeps the project's, so a new start can land after the project's
    old end (or a new deadline before its old start).
    """
    start, end = merged(start_date, project_start), merged(must_finish_by, project_end)
    if not inverted(start, end):
        return None
    if must_finish_by is None:
        return (
            f"startDate would fall after the project's target end date ({end}); "
            "set mustFinishBy as well"
        )
    return (
        f"mustFinishBy would fall before the project's start date ({start}); set startDate as well"
    )


def project_conflict(changes: dict[str, Any], saved: tuple[date | None, date | None]) -> str | None:
    """Why this project edit would invert step 5's saved dates, or None when it can't.

    `saved` is the constraint set's (start date, must-finish-by); a date the edit clears or
    leaves out keeps step 5's.
    """
    start = merged(changes.get("start_date"), saved[0])
    end = merged(changes.get("target_end_date"), saved[1])
    if not inverted(start, end):
        return None
    if changes.get("target_end_date") is None:
        return (
            f"startDate would fall after the must-finish-by date saved on step 5 ({end}); "
            "set targetEndDate as well"
        )
    return (
        f"targetEndDate would fall before the start date saved on step 5 ({start}); "
        "set startDate as well"
    )
