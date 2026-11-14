"""Project schedule and resource optimizer (Google OR-Tools CP-SAT).

Purpose: Turn a frozen planning input into the cheapest plan that respects the PM's hard
constraints, explain in plain English why there is none, and price the ways out. Pure code: no
database, FastAPI or settings imports, so every part is unit testable. Module map:
    model.py      planning input dataclasses + snapshot (de)serialisation
    outcome.py    resources and what a solve produces (plans, conflicts, outcome)
    calendar.py   working-day <-> date arithmetic
    baseline.py   "before" plan: forward pass with current owners, no levelling
    load.py       daily load arithmetic; how long a task takes with whom (part-timers stretch it)
    problem.py    numbers pre-computed for the model (candidates, durations, costs, horizon)
    builder.py    CP-SAT variables and constraints (main and explanation model)
    workload.py   each person's work modes, overtime and capacity in the model
    conflicts.py  plain-English texts of conflicting hard constraints
    solver.py     formulation docs, solving, status mapping, infeasibility explanation
    explain.py    UI payload (kpis, reassignments, scheduleChanges, utilization)
    report_context.py  lookups behind the payload (dates, hours, load, binding links)
    run_summary.py  timeline rows and plain-number summary added to the payload
    overrides.py  per-run what-ifs (budget, deadline, durations, hours) and baseline owners
    remedies.py   priced ways out of an infeasible plan ("What it would take")
    inputs.py     facts behind the confirmation sheet, inputs fingerprint
    input_sheet.py  confirmation sheet rows, sources and blockers
How it fits: The job runner (app/jobs/optimizer.py) feeds it the frozen
`optimization_runs.input_snapshot` and stores what `explain.build_report` returns;
layer: optimization.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""
