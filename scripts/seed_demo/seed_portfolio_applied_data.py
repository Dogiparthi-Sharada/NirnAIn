"""Demo seed data: the two portfolio projects with Plan v1 applied (the mockup's portfolio.js).

Purpose: Describe Portal Redesign (Plan v1 applied, then marked At Risk at 38 %, a design
sign-off milestone due before the plan reaches it) and ERP Migration (Plan v1 applied, On
Track at 64 %, a dry-run milestone after the planned finish), with the mockup's budgets,
due dates (moved to the demo year) and risks.
How it fits: `seed_portfolio_data.portfolio_specs` lists them between Warehouse Build and the
Workday draft; `seed_flows.Seeder.seed` seeds them through step 6; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import timedelta

from seed_model import (
    Deliverable,
    DemoCalendar,
    Limits,
    Link,
    Milestone,
    Person,
    ProjectSpec,
    WorkPackage,
)

TEST_AUTOMATION = "Test Automation"
REACT = "React"


def portal_redesign(calendar: DemoCalendar) -> ProjectSpec:
    """Plan v1 applied; the design sign-off milestone is due before the plan reaches it (soft)."""
    start, due = calendar.monday(-20), calendar.on(11, 14)
    return ProjectSpec(
        name="Portal Redesign",
        description=(
            "Rebuild the customer portal's account and checkout pages on the new design system."
        ),
        project_type="technology",
        department="Digital",
        sponsor="Head of Digital, Paul Greene",
        start=start,
        due=due,
        budget_cents=41_000_000,
        stage="applied",
        priority="high",
        methodology="agile",
        manual_risks=(
            "Front-end lead is booked at 105% for the next 6 weeks",
            "Accessibility audit is not scheduled",
        ),
        team=(
            Person("Nina Alvarez", "Front-end Lead", 40, (REACT, "Accessibility")),
            Person("Sam Patel", "UX Designer", 32, ("UX Design",)),
            Person(
                "Chris Young", "Front-end Developer", 40, (REACT,), contractor_rate_cents=12_000
            ),
            Person("Grace Kim", "QA Engineer", 20, (TEST_AUTOMATION,)),
        ),
        skills=((REACT, True), ("UX Design", True), (TEST_AUTOMATION, True)),
        deliverables=(
            Deliverable(
                "1.1",
                "Design",
                (
                    WorkPackage("1.1.1", "Design system refresh", 6, "Sam Patel", ("UX Design",)),
                    WorkPackage("1.1.2", "Design sign-off", 1, "Sam Patel", ("UX Design",)),
                ),
            ),
            Deliverable(
                "1.2",
                "Build",
                (
                    WorkPackage("1.2.1", "Account pages rebuild", 10, "Nina Alvarez", (REACT,)),
                    WorkPackage("1.2.2", "Checkout flow rebuild", 8, "Chris Young", (REACT,)),
                    WorkPackage("1.2.3", "Content and SEO migration", 5, "Chris Young"),
                ),
            ),
            Deliverable(
                "1.3",
                "Launch",
                (
                    WorkPackage("1.3.1", "Regression testing", 5, "Grace Kim", (TEST_AUTOMATION,)),
                    WorkPackage("1.3.2", "Launch and monitoring", 2, "Nina Alvarez"),
                ),
            ),
        ),
        links=(
            Link("1.1.1", "1.1.2"),
            Link("1.1.2", "1.2.1"),
            Link("1.1.2", "1.2.2"),
            Link("1.2.1", "1.3.1"),
            Link("1.2.2", "1.3.1"),
            Link("1.2.3", "1.3.1"),
            Link("1.3.1", "1.3.2"),
        ),
        limits=Limits(
            budget_cents=41_000_000,
            start=start,
            must_finish_by=due,
            specialist_answer="no",
            deadline_answer="hard",
            milestones=(Milestone("Design sign-off", calendar.monday(8), "1.1"),),
        ),
        status_after_apply="at_risk",
        progress_pct=38,
    )


def erp_migration(calendar: DemoCalendar) -> ProjectSpec:
    """Plan v1 applied, on track: the final dry run milestone comes after the planned finish."""
    start, due = calendar.monday(-36), calendar.on(12, 19)
    return ProjectSpec(
        name="ERP Migration",
        description=(
            "Move finance, procurement and inventory off the legacy ERP, with a data migration "
            "dry run before cutover."
        ),
        project_type="business_process",
        department="Finance",
        sponsor="CFO, Anita Rao",
        start=start,
        due=due,
        budget_cents=82_000_000,
        stage="applied",
        priority="high",
        methodology="hybrid",
        manual_risks=("Mapping for 2 legacy finance modules is not signed off yet",),
        team=(
            Person(
                "Ravi Menon", "ERP Functional Lead", 40, ("ERP Configuration", "Finance Processes")
            ),
            Person("Elena Petrova", "Data Migration Engineer", 40, ("Data Migration", "SQL")),
            Person("Owen Brooks", "Integration Developer", 32, ("Integration APIs",)),
            Person("Hannah Lee", "Test Lead", 24, (TEST_AUTOMATION,), contractor_rate_cents=9_500),
        ),
        skills=(
            ("ERP Configuration", True),
            ("Data Migration", True),
            ("Integration APIs", True),
            (TEST_AUTOMATION, True),
        ),
        deliverables=(
            Deliverable(
                "1.1",
                "Data Migration",
                (
                    WorkPackage(
                        "1.1.1",
                        "Finance module mapping sign-off",
                        3,
                        "Ravi Menon",
                        ("ERP Configuration",),
                    ),
                    WorkPackage(
                        "1.1.2",
                        "First data migration dry run",
                        6,
                        "Elena Petrova",
                        ("Data Migration",),
                    ),
                    WorkPackage(
                        "1.1.3", "Reconciliation fixes", 4, "Elena Petrova", ("Data Migration",)
                    ),
                ),
            ),
            Deliverable(
                "1.2",
                "Integrations",
                (
                    WorkPackage(
                        "1.2.1",
                        "Payroll and banking interfaces",
                        8,
                        "Owen Brooks",
                        ("Integration APIs",),
                    ),
                    WorkPackage(
                        "1.2.2",
                        "Procurement workflow configuration",
                        6,
                        "Ravi Menon",
                        ("ERP Configuration",),
                    ),
                    WorkPackage(
                        "1.2.3",
                        "Inventory and warehouse interfaces",
                        6,
                        "Owen Brooks",
                        ("Integration APIs",),
                    ),
                ),
            ),
            Deliverable(
                "1.3",
                "Testing & Cutover",
                (
                    WorkPackage(
                        "1.3.1", "System integration testing", 6, "Hannah Lee", (TEST_AUTOMATION,)
                    ),
                    WorkPackage(
                        "1.3.2", "User acceptance testing", 6, "Hannah Lee", (TEST_AUTOMATION,)
                    ),
                    WorkPackage(
                        "1.3.3",
                        "Final data migration dry run",
                        2,
                        "Elena Petrova",
                        ("Data Migration",),
                    ),
                    WorkPackage("1.3.4", "Go-live and hypercare", 4, "Ravi Menon"),
                ),
            ),
        ),
        links=(
            Link("1.1.1", "1.1.2"),
            Link("1.1.2", "1.1.3"),
            Link("1.1.3", "1.3.1"),
            Link("1.2.1", "1.3.1"),
            Link("1.2.2", "1.3.1"),
            Link("1.2.3", "1.3.1"),
            Link("1.3.1", "1.3.2"),
            Link("1.3.2", "1.3.3"),
            Link("1.3.3", "1.3.4"),
        ),
        limits=Limits(
            budget_cents=82_000_000,
            start=start,
            must_finish_by=due,
            specialist_answer="no",
            deadline_answer="hard",
            milestones=(Milestone("Data migration dry run", due - timedelta(weeks=8), "1.3"),),
        ),
        status_after_apply="on_track",
        progress_pct=64,
    )
