"""Demo seed data: the Enterprise Data Platform walkthrough, as in the approved mockup.

Purpose: Restate the mockup's 7-step walkthrough for the real API: the kickoff notes are
written so the rules extractor (no Claude) finds the mockup's 20 scope items word for word,
the WBS is the mockup's 5 deliverables and 15 work packages (weeks → working days), the team
is the mockup's five people, and step 7 records one Steering Committee change (Plan v2).
Sources: the mockup's data/scope-items.js, wbs.js, team.js, plans.js and views/intake.js,
constraints.js, changes.js.
How it fits: `seed_demo.py` passes `walkthrough_spec(calendar)` to `seed_flows.Seeder.seed`
last, so it is the newest project; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from seed_model import (
    ChangeSpec,
    Deliverable,
    DemoCalendar,
    Limits,
    Link,
    Milestone,
    Person,
    ProjectSpec,
    WorkPackage,
)

WALKTHROUGH_NAME = "Enterprise Data Platform"
ATTENDEES = (
    "Robert Walsh (VP Eng), Jane Doe (PM), Sara Kim (Lead Architect), Mark Chen (Data Engineer)"
)

DATA_MODELING = "Data Modeling"
SAP = "SAP Integration"
SPARK = "Spark / Kafka"
COMPLIANCE = "SOC 2 / GDPR Compliance"
BI = "Power BI / Tableau"
DEVOPS = "Kubernetes / DevOps"

# data/team.js. The mockup gives no worker types: the four core people are employees (company
# rate), and the part-time security engineer is an outside contractor, which shows the type.
TEAM = (
    Person("Sara Kim", "Lead Architect", 40, (DATA_MODELING, SPARK)),
    Person("Mark Chen", "Data Engineer", 40, (SPARK, DATA_MODELING)),
    Person("Tom Rivera", "BI Developer", 32, (BI,)),
    Person("Lisa Park", "DevOps", 20, (DEVOPS,)),
    Person("James Wu", "Security Eng.", 16, (COMPLIANCE,), contractor_rate_cents=14_000),
)

# data/team.js SKILLS: (name, required).
SKILLS = (
    (DATA_MODELING, True),
    (SAP, True),
    (SPARK, True),
    (COMPLIANCE, True),
    (BI, True),
    (DEVOPS, False),
)

# data/wbs.js: [code, name, weeks, owner]. "J. Doe" (the PM) and "External" are not on the
# mockup's team, so those packages go to the team member closest to the work.
DELIVERABLES = (
    Deliverable(
        "1.1",
        "Data Ingestion Layer",
        (
            WorkPackage("1.1.1", "Salesforce connector development", 3, "Mark Chen", (SPARK,)),
            WorkPackage("1.1.2", "SAP integration setup", 4, "Mark Chen", (SAP,)),
            WorkPackage(
                "1.1.3", "Internal DB pipeline scripting (12 sources)", 6, "Sara Kim", (SPARK,)
            ),
            WorkPackage(
                "1.1.4", "Data quality validation framework", 2, "Sara Kim", (DATA_MODELING,)
            ),
        ),
    ),
    Deliverable(
        "1.2",
        "Data Warehouse & ETL",
        (
            WorkPackage(
                "1.2.1", "Schema design and data modeling", 3, "Sara Kim", (DATA_MODELING,)
            ),
            WorkPackage("1.2.2", "ETL pipeline development", 5, "Mark Chen", (SPARK,)),
            WorkPackage(
                "1.2.3", "Performance tuning and indexing", 2, "Sara Kim", (DATA_MODELING,)
            ),
        ),
    ),
    Deliverable(
        "1.3",
        "Dashboard & Reporting Portal",
        (
            WorkPackage("1.3.1", "BI tool configuration and setup", 2, "Tom Rivera", (BI,)),
            WorkPackage("1.3.2", "Standard report library (40 reports)", 4, "Tom Rivera", (BI,)),
            WorkPackage("1.3.3", "Self-service analytics layer", 3, "Tom Rivera", (BI,)),
        ),
    ),
    Deliverable(
        "1.4",
        "Security & Compliance",
        (
            WorkPackage("1.4.1", "RBAC implementation", 2, "Sara Kim"),
            WorkPackage("1.4.2", "Audit logging system", 1, "Mark Chen"),
            WorkPackage("1.4.3", "SOC 2 / GDPR compliance review", 3, "James Wu", (COMPLIANCE,)),
        ),
    ),
    Deliverable(
        "1.5",
        "Training & Rollout",
        (
            WorkPackage("1.5.1", "End-user training materials", 2, "Tom Rivera"),
            WorkPackage("1.5.2", "Phased rollout (300 users)", 3, "Lisa Park", (DEVOPS,)),
        ),
    ),
)

# data/team.js DEPS (the first five), plus the order the mockup's Gantt (plans.js) implies:
# validation after the pipelines, tuning after ETL, the BI layer after BI setup, the compliance
# review after RBAC and audit logging, training after the reports, rollout last.
LINKS = (
    Link("1.1.1", "1.2.2"),
    Link("1.1.2", "1.2.2"),
    Link("1.2.1", "1.2.2"),
    Link("1.2.2", "1.3.1"),
    Link("1.4.1", "1.3.3", "SS"),
    Link("1.1.3", "1.1.4"),
    Link("1.2.2", "1.2.3"),
    Link("1.3.1", "1.3.2"),
    Link("1.3.1", "1.3.3"),
    Link("1.4.1", "1.4.3"),
    Link("1.4.2", "1.4.3"),
    Link("1.3.2", "1.5.1"),
    Link("1.3.3", "1.5.1"),
    Link("1.4.3", "1.5.2"),
    Link("1.5.1", "1.5.2"),
)


def kickoff_notes(calendar: DemoCalendar) -> str:
    """views/intake.js notes, rewritten under the rules extractor's headings so each bullet is
    one of the mockup's scope items (data/scope-items.js, after the PM's fixes)."""
    year = calendar.year
    return f"""Meeting Notes — Enterprise Data Platform Kickoff
Date: {calendar.today_label} | Attendees: {ATTENDEES}

OBJECTIVES
• Consolidate 7 legacy BI systems into a unified data platform
• Enable real-time dashboards for all business units by Q3 {year}
• Reduce reporting cycle time from 3 days to 4 hours

REQUIREMENTS
• Ingestion layer supporting Salesforce, SAP, and 12 internal databases
• Sub-5-second query response for standard reports
• Role-based access control (RBAC) with full audit logging
• GDPR and SOC 2 Type II compliance required

DELIVERABLES
• Data ingestion pipeline (Phase 1: Salesforce, SAP)
• Unified data warehouse schema and ETL processes
• Self-service dashboard portal for 500+ users
• Security and compliance documentation package

CONSTRAINTS
• Budget approved at $375,000
• Target go-live: Q3 {year} (September 30)
• Only 2 data engineers available

RISKS & CONCERNS
• SAP integration timeline uncertain — vendor dependency may delay Phase 1
• Infrastructure costs may exceed budget if cloud spend spikes
• End-user adoption risk — 300 users need training with no plan yet

OPEN ITEMS / UNKNOWNS
• Security review process and timeline not initiated
• Disaster recovery SLA not defined
• User training and change management plan not scoped
"""


def steering_notes(calendar: DemoCalendar) -> str:
    """The step-7 change (views/changes.js style): the BI developer's hours are cut."""
    return f"""Steering Committee — {calendar.today_label}
Attendees: {ATTENDEES}

• Robert: Tom Rivera is needed on the Portal Redesign, so Tom Rivera can give this project \
only 24 hours a week from now on.
• Sara: Internal database pipelines are on track.
"""


def walkthrough_spec(calendar: DemoCalendar) -> ProjectSpec:
    """The walkthrough project, applied as Plan v1 and changed into Plan v2."""
    year = calendar.year
    start, go_live = calendar.on(1, 15), calendar.on(9, 30)
    return ProjectSpec(
        name=WALKTHROUGH_NAME,
        description=(
            "Consolidate 7 legacy BI systems into one platform with real-time dashboards for "
            "every business unit."
        ),
        project_type="technology",
        department="Engineering",
        sponsor="VP of Engineering, Robert Walsh",
        start=start,
        due=go_live,
        budget_cents=37_500_000,
        stage="applied",
        intake_notes=kickoff_notes(calendar),
        # The rules extractor also files these two bullets under Constraints; the PM removes
        # the copies.
        remove_scope=(
            ("constraint", f"Enable real-time dashboards for all business units by Q3 {year}"),
            ("constraint", "Infrastructure costs may exceed budget if cloud spend spikes"),
        ),
        team=TEAM,
        skills=SKILLS,
        deliverables=DELIVERABLES,
        links=LINKS,
        # views/constraints.js: budget Must hold, $280K labor / $95K infra caps, 0% reserve,
        # Sep 30 a hard deadline, an outside SAP specialist allowed at $175/h, soft phase
        # milestones.
        limits=Limits(
            budget_cents=37_500_000,
            start=start,
            must_finish_by=go_live,
            labor_cap_cents=28_000_000,
            infra_cap_cents=9_500_000,
            specialist_answer="yes",
            deadline_answer="hard",
            contractor_rate_cents=17_500,
            milestones=(
                Milestone("Phase 1 Milestone (Ingestion)", calendar.on(4, 15), "1.1"),
                Milestone("Phase 2 Milestone (Dashboard)", calendar.on(7, 31), "1.3"),
            ),
        ),
        validate_wbs=True,
        changes=(
            ChangeSpec(
                "Steering Committee: Tom Rivera down to 24 h/week",
                steering_notes(calendar),
                expect_kind="capacity",
                expect_target="Tom Rivera",
                edit_hours=("Tom Rivera", 24),
            ),
        ),
    )
