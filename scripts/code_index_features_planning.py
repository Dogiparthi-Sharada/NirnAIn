"""Curated feature map for INDEX.txt, part 2: optimization, plans, changes, dashboard and tests.

Purpose: The "where is the code for ...?" entries of steps 6-7, the dashboard and the tests, in the
same (question, file path, symbol, explanation) form as `code_index_features.py`.
How it fits: Split out of `code_index_features.py` to keep each file under 500 lines; that module
imports these groups into FEATURE_MAP, which `build_code_index.py` renders; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

FeatureEntry = tuple[str, str, str, str]

OPTIMIZATION = (
    "F. Step 6 - Optimization (Google OR-Tools CP-SAT)",
    [
        (
            "What is checked before a run may start?",
            "backend/app/optimization/input_sheet.py",
            "build_sheet",
            "Builds the 'Check the inputs' sheet in screen order and lists what still blocks the "
            "run (no team, unconfirmed estimates, unanswered questions).",
        ),
        (
            "Where is a run queued?",
            "backend/app/api/v1/optimization.py",
            "create_run",
            "Freezes every input into a snapshot so the run is reproducible; returns 422 with "
            "what is missing.",
        ),
        (
            "How is a second run at the same time prevented?",
            "backend/app/repositories/optimization.py",
            "OptimizationRepository.lock_run_creation",
            "A Postgres advisory lock per project, held until the transaction ends, so a "
            "double-click can't start two runs.",
        ),
        (
            "How does the app know the inputs changed since the plan?",
            "backend/app/optimization/inputs.py",
            "inputs_hash",
            "SHA-256 of exactly what the solver uses (not today's date or other projects' "
            "bookings); compared with the applied plan's hash.",
        ),
        (
            "Who runs the solver?",
            "backend/app/jobs/optimizer.py",
            "OptimizationRunner._run",
            "Claims a queued run, solves its frozen input on a worker thread, prices remedies if "
            "needed and stores the report.",
        ),
        (
            "Where is the mathematical model (variables, constraints, objective)?",
            "backend/app/optimization/solver.py",
            "solve",
            "The module docstring has the full formulation: one assignment boolean per task and "
            "person, duration set by the person, capacity, skills, dependencies, deadline and "
            "budget; objective in cents.",
        ),
        (
            "Where are the CP-SAT variables and constraints added?",
            "backend/app/optimization/builder.py",
            "ModelBuilder.build",
            "Adds every task, the workload, dependencies, makespan, deadlines and budget, then "
            "minimizes the sum of costs and penalties.",
        ),
        (
            "How does skill level change speed?",
            "backend/app/optimization/speed.py",
            "pace_percent",
            "10% faster per level above the minimum (at most 30%); 25% slower per level below.",
        ),
        (
            "How are part-time hours and overtime limited?",
            "backend/app/optimization/load.py",
            "flex_capacity_tenths",
            "'Can flex' allows up to 25% over contracted hours; `task_mode` stretches a task for "
            "part-timers.",
        ),
        (
            "How is a plan's cost computed?",
            "backend/app/optimization/costing.py",
            "plan_cost",
            "Each task's remaining hours at the person's pace times their rate, plus the overtime"
            " premium; all in cents.",
        ),
        (
            "What is the 'before optimization' baseline?",
            "backend/app/optimization/baseline.py",
            "baseline_plan",
            "Current owners at WBS durations, as early as dependencies allow; finished work keeps"
            " its dates.",
        ),
        (
            "How is useless overtime removed?",
            "backend/app/optimization/polish.py",
            "without_overtime",
            "Re-solves without overtime and keeps that plan if it achieves the same dates and "
            "cost.",
        ),
        (
            "How is an impossible plan explained?",
            "backend/app/optimization/blockers.py",
            "explain_infeasibility",
            "Puts each hard constraint group behind an assumption and extracts every independent "
            "conflict, worded in plain English.",
        ),
        (
            "Where are the priced options when the date slips?",
            "backend/app/optimization/remedies.py",
            "price_remedies",
            "Re-solves relaxed variants (bring in specialists, allow overtime, move the deadline,"
            " or a combination) and offers the ones that help, with their cost.",
        ),
        (
            "Which date does a re-plan aim for?",
            "backend/app/optimization/model.py",
            "target_of",
            "The plan in force's finish date for a re-plan, otherwise the project's "
            "must-finish-by date.",
        ),
        (
            "Where are KPIs and 'why did this move' reasons built?",
            "backend/app/optimization/explain.py",
            "build_report",
            "Turns the solution into KPI cards, rows to store and per-task reasons (skill gap, "
            "over-allocation, cost, waiting).",
        ),
        (
            "Screen: optimization results",
            "src/screens/OptimizationResults.tsx",
            "OptimizationResults",
            "Inputs sheet, solver status, Gantt (optimization/PlanGantt.tsx) and the 'keep the "
            "date' options.",
        ),
    ],
)

PLAN = (
    "G. Plan v1 - apply and export",
    [
        (
            "Where is a run applied as the next plan version?",
            "backend/app/api/v1/plan.py",
            "apply_run",
            "Refuses with 409 if the inputs changed since the run (stale plan); otherwise calls "
            "the SQL function.",
        ),
        (
            "What does applying a plan write?",
            "supabase/migrations/20261113000600_apply_allocation_hours.sql",
            "public.apply_optimization_run",
            "One transaction: task owners and dates, booked hours per week, and the new plan "
            "version.",
        ),
        (
            "Which plan is in force?",
            "backend/app/repositories/optimization.py",
            "OptimizationRepository.applied_run",
            "The run applied last, with the inputs hash it was approved with.",
        ),
        (
            "Where are the PDF and MS Project exports?",
            "backend/app/export/pdf.py",
            "render_pdf",
            "Landscape PDF of the schedule; export/msproject.py `render_msproject` writes MSPDI "
            "XML.",
        ),
        (
            "Browser side of applying a plan",
            "src/lib/queries/optimization.ts",
            "useApplyRun",
            "TanStack Query mutation that applies the run and refreshes the project's data.",
        ),
    ],
)

CHANGES = (
    "H. Step 7 - Changes and versions (Plan v1 → v2)",
    [
        (
            "Where is a mid-project change recorded?",
            "backend/app/api/v1/changes.py",
            "create_change",
            "Stores the change (notes, edits or team changes) and finds what it affects.",
        ),
        (
            "How are changes found in meeting notes without AI?",
            "backend/app/changes/detect.py",
            "detect",
            "Rules read budget, deadline, durations, hours, people and new scope from each "
            "sentence, keep its verbatim quote, and turn anything unclear into a question.",
        ),
        (
            "How does Claude read the notes?",
            "backend/app/changes/llm.py",
            "ChangeDetector.detect",
            "Structured output checked against the notes; falls back to the rules if Claude fails.",
        ),
        (
            "Which questions does a change ask?",
            "backend/app/changes/questions.py",
            "build_questions",
            "Values that conflict with ones you confirmed are asked, never silently overwritten.",
        ),
        (
            "How do answers become a re-plan?",
            "backend/app/changes/derive.py",
            "derive_overrides",
            "Turns the change and its answers into what-if overrides applied to the frozen inputs.",
        ),
        (
            "How is Plan vN+1 compared with Plan vN?",
            "backend/app/changes/compare.py",
            "build_comparison",
            "Per work package: old and new dates, the shift, and why it moved.",
        ),
        (
            "Where is a change approved?",
            "backend/app/api/v1/changes.py",
            "approve_change",
            "The re-plan becomes the next plan version and the change's values become the "
            "project's inputs.",
        ),
        (
            "Why doesn't an approved plan look outdated right away?",
            "backend/app/repositories/changes.py",
            "ChangeRepository.record_inputs_in_force",
            "Saves the inputs hash at approval, so the plan in force matches its own inputs.",
        ),
        (
            "Screen: changes and versions",
            "src/screens/ChangesVersions.tsx",
            "ChangesVersions",
            "Report a change, answer questions, re-plan, compare (changes/PlanComparison.tsx) and"
            " approve.",
        ),
    ],
)

DASHBOARD = (
    "I. Dashboard and planning assistant",
    [
        (
            "How is the per-project dashboard built?",
            "backend/app/domain/overview.py",
            "build_overview",
            "Combines status, budget, milestones, delivery health, risks and the next step from "
            "stored facts.",
        ),
        (
            "Where are the KPI cards drawn?",
            "src/components/DashboardCards.tsx",
            "DashboardKpis",
            "Budget, completion, utilization and plan cards from the overview response.",
        ),
        (
            "Screen: dashboard",
            "src/screens/Dashboard.tsx",
            "Dashboard",
            "Project picker, KPI cards, health, risks, notifications and AI findings.",
        ),
        (
            "Where is the planning assistant?",
            "backend/app/api/v1/assistant.py",
            "ask_assistant",
            "Answers questions about the current page and project using its live context "
            "(ai/assistant.py).",
        ),
    ],
)

TESTS = (
    "J. Tests and tooling",
    [
        (
            "Backend unit and API tests (pytest)",
            "backend/tests",
            "",
            "Run with `cd backend && uv run pytest` (521 tests); folders mirror the app packages.",
        ),
        (
            "Database security tests (pgTAP)",
            "supabase/tests/database",
            "",
            "Check RLS, deletes, applying a run and uploads directly in Postgres.",
        ),
        (
            "End-to-end API scenarios",
            "tests/e2e/api/run_api_suite.py",
            "main",
            "About 240 business scenarios across all 7 steps against a running API, without "
            "Claude tokens.",
        ),
        (
            "Demo data seed (Jane Doe's projects)",
            "scripts/seed_demo/seed_demo.py",
            "main",
            "Creates the demo account and five projects through the public API, up to Plan v2.",
        ),
    ],
)
