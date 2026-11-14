"""Demo seed data: the dashboard's other projects (the mockup's portfolio.js).

Purpose: Make the Dashboard picker look like the mockup's portfolio with real projects (the
API has no "sample" flag). The mockup's three samples keep their names, budgets, due dates
(moved to the demo year), statuses, progress, milestones and risks where the product can
hold them; a fourth project is a draft, so the picker shows every stage:
- Warehouse Build: in planning (scope, WBS, team and limits in place; the 2 questions open),
- Portal Redesign: Plan v1 applied, then marked At Risk at 38 %,
- ERP Migration: Plan v1 applied, On Track at 64 %,
- Workday HR Integration: a draft saved from the create form.
How it fits: `portfolio_specs(calendar)` lists them in creation order (oldest first, as the
mockup's project ids are), taking the two applied ones from `seed_portfolio_applied_data`;
`seed_flows.Seeder.seed` seeds each one; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

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
from seed_portfolio_applied_data import erp_migration, portal_redesign


def warehouse_notes(calendar: DemoCalendar) -> str:
    """Planning workshop notes for the rules extractor (risks from portfolio.js)."""
    return f"""Warehouse Build — Planning Workshop
Date: {calendar.today_label} | Attendees: Dev Shah (PM), Carlos Diaz (Site Manager), \
Priya Nair (WMS)

OBJECTIVES
• Open the new regional distribution warehouse for the peak season
• Double pick-and-pack capacity for the West region

REQUIREMENTS
• 4,200 pallet positions of selective racking
• Warehouse management system connected to the ERP
• Forklift certification for 12 new staff

RISKS & CONCERNS
• Racking supplier delivery slipped 9 days
• Oct 31 go-live is at risk unless install crews double up
• Forklift certification for 12 new staff is not booked

OPEN ITEMS / UNKNOWNS
• Fire inspection date not confirmed
"""


def warehouse_build(calendar: DemoCalendar) -> ProjectSpec:
    """In planning: everything up to step 5 except the two questions (next step: answer them)."""
    start, due = calendar.monday(3), calendar.on(10, 31)
    return ProjectSpec(
        name="Warehouse Build",
        description=(
            "Fit out the new regional warehouse: racking, WMS, scanners and trained staff "
            "before peak season."
        ),
        project_type="construction",
        department="Operations",
        sponsor="COO, Martin Hale",
        start=start,
        due=due,
        budget_cents=54_000_000,
        stage="planning",
        intake_notes=warehouse_notes(calendar),
        team=(
            Person("Carlos Diaz", "Site Manager", 40, ("Construction Management",)),
            Person("Priya Nair", "WMS Analyst", 32, ("WMS Configuration",)),
            Person(
                "Marco Rossi",
                "Racking Install Lead",
                40,
                ("Racking Installation",),
                contractor_rate_cents=9_000,
            ),
        ),
        skills=(
            ("Construction Management", True),
            ("WMS Configuration", True),
            ("Racking Installation", True),
        ),
        deliverables=(
            Deliverable(
                "1.1",
                "Site & Racking",
                (
                    WorkPackage(
                        "1.1.1",
                        "Floor preparation and marking",
                        2,
                        "Carlos Diaz",
                        ("Construction Management",),
                    ),
                    WorkPackage(
                        "1.1.2", "Racking install", 5, "Marco Rossi", ("Racking Installation",)
                    ),
                    WorkPackage(
                        "1.1.3",
                        "Safety and fire inspection",
                        1,
                        "Carlos Diaz",
                        ("Construction Management",),
                    ),
                ),
            ),
            Deliverable(
                "1.2",
                "Systems",
                (
                    WorkPackage(
                        "1.2.1",
                        "WMS configuration and ERP link",
                        4,
                        "Priya Nair",
                        ("WMS Configuration",),
                    ),
                    WorkPackage("1.2.2", "Scanner and label printer rollout", 2, "Priya Nair"),
                ),
            ),
            Deliverable(
                "1.3",
                "People & Go-live",
                (
                    WorkPackage("1.3.1", "Forklift certification (12 staff)", 2, "Carlos Diaz"),
                    WorkPackage("1.3.2", "Go-live and stock transfer", 2, "Carlos Diaz"),
                ),
            ),
        ),
        links=(
            Link("1.1.1", "1.1.2"),
            Link("1.1.2", "1.1.3"),
            Link("1.2.1", "1.2.2"),
            Link("1.1.3", "1.3.2"),
            Link("1.2.2", "1.3.2"),
            Link("1.3.1", "1.3.2"),
        ),
        limits=Limits(
            budget_cents=54_000_000,
            start=start,
            must_finish_by=due,
            milestones=(Milestone("Racking install", calendar.on(6, 30), "1.1"),),
        ),
    )


def workday_draft(calendar: DemoCalendar) -> ProjectSpec:
    """A draft saved from the create form (name, dates and budget only)."""
    return ProjectSpec(
        name="Workday HR Integration",
        description=(
            "Connect Workday to payroll and the data platform so HR reports come from one source."
        ),
        project_type="technology",
        department="People & HR",
        sponsor="CHRO, Laura Bennett",
        start=calendar.on(3, 1),
        due=calendar.on(8, 31),
        budget_cents=26_000_000,
        stage="draft",
    )


def portfolio_specs(calendar: DemoCalendar) -> list[ProjectSpec]:
    """The non-walkthrough projects, oldest first (the walkthrough comes last, so it is newest)."""
    return [
        warehouse_build(calendar),
        portal_redesign(calendar),
        erp_migration(calendar),
        workday_draft(calendar),
    ]
