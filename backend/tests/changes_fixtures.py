"""Shared step 7 test data: the approved mockup's Steering Committee notes and a project
context with three work packages and two people. Used by test_changes_rules / _api."""

from __future__ import annotations

import uuid
from datetime import date

from app.changes.context import ChangeContext, PersonRef, TaskRef

NOTES = """Steering Committee — March 3, 2025
Attendees: Robert Walsh (VP Eng), Sara Kim (Lead Architect), Mark Chen (Data Engineer)

• Robert: Workday has to be added as a data source. It must be in the platform at go-live.
• Robert: Finance is trimming the budget to $365K.
• Mark: The Salesforce connector is done.
• SAP vendor update: sandbox access slips two weeks.
• Sara: Internal database pipelines are on track."""

MARK, SARA = uuid.uuid4(), uuid.uuid4()
SALESFORCE, SAP, ETL = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def project_context() -> ChangeContext:
    """Plan v1 in force: $375,000 budget, Sep 30, 2025 deadline, SAP → ETL dependency."""
    return ChangeContext(
        budget_cents=37_500_000,
        deadline=date(2025, 9, 30),
        start=date(2025, 1, 13),
        tasks=(
            TaskRef(SALESFORCE, "1.1.1", "Salesforce connector development", 15, MARK),
            TaskRef(SAP, "1.1.2", "SAP integration setup", 20, None),
            TaskRef(ETL, "1.2.2", "ETL pipeline development", 25, MARK),
        ),
        people=(PersonRef(MARK, "Mark Chen", 30), PersonRef(SARA, "Sara Kim", 32)),
        edges=((SAP, ETL),),
        plan_version=1,
        plan_cost_cents=34_860_000,
        plan_finish=date(2025, 9, 19),
        today=date(2025, 3, 3),
    )
