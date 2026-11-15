"""Tests that done work stays done (round 3, R2).

Purpose: QA's CHANGE-010: W01/W04 vanished from every re-plan after the approval. A package an
approved progress change set to 0 days stays as finished work (done, at the dates the latest applied
plan gave it, needing no skills); a 0-day package nothing ever scheduled is not work.
How it fits: Compiles the loader's SQL (app/repositories/optimization.py) against the recording
connection of tests/support/connections.py and solves the small input of
tests/support/solver_inputs.py; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date
from typing import Any

from app.optimization.explain import build_report
from app.optimization.model import Task
from app.optimization.solver import solve
from app.repositories.optimization import OptimizationRepository
from tests.support.connections import Conn, Rows, project_context
from tests.support.solver_inputs import FAST, make_input


def _node(
    node_id: uuid.UUID, parent: uuid.UUID | None, level: int, code: str, **values: Any
) -> dict[str, Any]:
    """A `wbs_nodes_with_code` row."""
    row = {
        "id": node_id,
        "parent_id": parent,
        "level": level,
        "name": f"Node {code}",
        "description": None,
        "duration_days": None,
        "effort_hours": None,
        "owner_employee_id": None,
        "code": code,
    }
    return row | values


def _listed(params: dict[str, Any]) -> set[str]:
    """The ids an `IN (…)` filter of a compiled statement lists."""
    return {
        str(value) for listed in params.values() if isinstance(listed, list) for value in listed
    }


async def test_finished_work_is_loaded_done_at_its_applied_dates() -> None:
    """Finished work loads done at its applied dates; unscheduled 0-day packages are dropped."""
    root, deliverable, done, never, open_work = (uuid.uuid4() for _ in range(5))
    connection = Conn(
        Rows(
            [
                _node(root, None, 1, "1"),
                _node(deliverable, root, 2, "1.1"),
                _node(done, deliverable, 3, "1.1.1", duration_days=0),
                _node(never, deliverable, 3, "1.1.2", duration_days=0),
                _node(open_work, deliverable, 3, "1.1.3", duration_days=5),
            ]
        ),
        Rows([{"node_id": str(done), "first_day": "2026-10-05", "last_day": "2026-10-09"}]),
        Rows([]),  # skills tagged on the open work
        Rows([]),  # the project's skill list
        Rows([]),  # no project team
        Rows([]),  # no org employees
        Rows([]),  # dependencies
        Rows([]),  # constraint set
        Rows([]),  # milestones
    )
    planning_input = await OptimizationRepository(connection).load_input(
        project_context(), date(2026, 10, 21)
    )  # type: ignore[arg-type]

    assert [(task.code, task.done) for task in planning_input.tasks] == [
        ("1.1.1", True),
        ("1.1.3", False),
    ]
    finished = planning_input.tasks[0]
    assert (finished.duration_days, finished.applied_start, finished.applied_end) == (
        0,
        date(2026, 10, 5),
        date(2026, 10, 9),
    )
    assert "jsonb_array_elements" in connection.sql[1]
    assert "applied_at IS NOT NULL" in connection.sql[1]
    assert _listed(connection.params[1]) == {str(done), str(never)}
    # Finished work needs nobody: only the open package's skills are read.
    assert _listed(connection.params[2]) == {str(open_work)}


def test_finished_work_with_no_days_left_stays_in_the_plan() -> None:
    """The solver and report keep finished work at its dates, and it costs nothing."""
    finished = Task(
        "a",
        "1.1.1",
        "Design",
        0,
        owner_id="e1",
        done=True,
        applied_start=date(2026, 10, 5),
        applied_end=date(2026, 10, 9),
    )
    planning_input = make_input(tasks=(finished, Task("b", "1.1.2", "Build", 10, owner_id="e1")))
    planning_input = dataclasses.replace(planning_input, today=date(2026, 10, 12))
    outcome = solve(planning_input, FAST)
    assert outcome.status == "optimal"
    report = build_report(planning_input, outcome)
    assert report.result is not None
    timeline = {row["wbsNodeId"]: row for row in report.result["schedule"]}
    assert (timeline["a"]["start"], timeline["a"]["end"], timeline["a"]["done"]) == (
        "2026-10-05",
        "2026-10-09",
        True,
    )
    assert report.expected_cost_cents == 80 * 10_000  # only Build's 80 hours cost anything
