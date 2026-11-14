"""Planning inputs and solve options for the optimizer tests.

Purpose: Build small, readable optimizer inputs (two tasks, one link, two people by default) that
each test varies with keyword overrides.
How it fits: Imported by the solver, explain, remedies, inputs and job tests; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from app.optimization.model import (
    Constraints,
    Dependency,
    Employee,
    PlanningInput,
    SkillRequirement,
    Task,
)
from app.optimization.solver import (
    SolveOptions,
)

MONDAY = date(2026, 10, 5)


FAST = SolveOptions(time_limit_s=10, workers=2)


SAP = SkillRequirement("sap", "SAP", 4)


def make_input(**overrides: Any) -> PlanningInput:
    """2 tasks, 1 FS dependency, 2 employees."""
    base: dict[str, Any] = {
        "project_id": "p1",
        "project_name": "CRM",
        "start_date": MONDAY,
        "tasks": (
            Task("a", "1.1.1", "Design", 5, owner_id="e1"),
            Task("b", "1.1.2", "Build", 10, owner_id="e1"),
        ),
        "employees": (
            Employee("e1", "Mark Chen", hourly_rate_cents=10_000),
            Employee("e2", "Lisa Park", hourly_rate_cents=12_000, skills={"sap": 5}),
        ),
        "dependencies": (Dependency("a", "b"),),
        "constraints": Constraints(),
    }
    base.update(overrides)
    return PlanningInput(**base)


def with_constraints(planning_input: PlanningInput, **changes: Any) -> PlanningInput:
    """Return the input with some constraint fields changed."""
    return dataclasses.replace(
        planning_input, constraints=dataclasses.replace(planning_input.constraints, **changes)
    )


DATA = SkillRequirement("data", "Data Engineering", 3)


def leo_martins(hours: float = 40) -> Employee:
    """Return Leo Martins, a data engineer at the minimum level for data work, at `hours` a week."""
    return Employee(
        "leo",
        "Leo Martins",
        weekly_capacity_hours=hours,
        hourly_rate_cents=9_000,
        skills={"data": 3},  # at the minimum: the WBS pace (D5)
    )


def data_pipeline_input() -> PlanningInput:
    """Leo is the only data engineer: Ingest (10 d, 80 h) then Model (5 d, 40 h)."""
    return make_input(
        tasks=(
            Task("a", "1.1.1", "Ingest", 10, effort_hours=80, owner_id="leo", skills=(DATA,)),
            Task("b", "1.1.2", "Model", 5, effort_hours=40, owner_id="leo", skills=(DATA,)),
            Task("c", "1.1.3", "Docs", 5, owner_id="ana"),
        ),
        employees=(leo_martins(), Employee("ana", "Ana Souza", hourly_rate_cents=8_000)),
        dependencies=(Dependency("a", "b"),),
        constraints=Constraints(must_finish_by=date(2027, 3, 31)),
    )
