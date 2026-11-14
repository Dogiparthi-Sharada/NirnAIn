"""Project schedule + resource optimizer (Google OR-Tools CP-SAT).

Pure code: no database, FastAPI or settings imports, so every part is unit
testable. The job runner (`app/jobs/optimizer.py`) feeds it the frozen
`optimization_runs.input_snapshot` and stores what `explain.build_report`
returns.

    model.py      planning input / output dataclasses + snapshot (de)serialisation
    calendar.py   working-day <-> date arithmetic
    baseline.py   "before" plan: forward pass with current owners, no levelling
    solver.py     CP-SAT model, status mapping, infeasibility explanation
    explain.py    UI payload (kpis, reassignments, scheduleChanges, utilization)
    run_summary.py  timeline rows and plain-number summary added to the payload
    overrides.py  per-run what-ifs (budget, deadline, durations, hours) and baseline owners
    remedies.py   priced ways out of an infeasible plan ("What it would take")
    inputs.py     facts behind the confirmation sheet, inputs fingerprint
    input_sheet.py  confirmation sheet rows, sources and blockers
"""
