# API end-to-end scenarios

A scenario suite that drives a **running** NirnAIn API the way a project manager would. It
signs in, creates fake projects in its own workspace, walks every planning step and every
mid-project change, and logs each scenario (expected vs actual) in a markdown report.

The suite uses only the Python 3.12 standard library (`urllib`), so it needs no install. It
calls only public endpoints: Supabase Auth for the accounts and `/api/v1` for everything
else. It never touches the database directly.

> [!IMPORTANT]
> Run it only against an API started **without** `ANTHROPIC_API_KEY`. Intake, WBS
> generation and validation, change detection and the draft reader then use the built-in
> rules and templates, so the run costs **0 Claude tokens**. Many checks expect that
> behaviour (for example model `rules`, or chat answering 503). Never run it against
> production: it creates accounts and projects.

## What it covers

About 240 scenarios in these areas, in this order:

| Area | What is checked | Module |
|---|---|---|
| 1. Create project | Valid create; empty, too long, negative or backwards values refused; Unicode round trip; list, update, delete | `scenarios_create_intake_scope.py` |
| 2. AI intake and uploads | Video and `.exe` refused, empty files, `.vtt` transcript and audio kept; one active intake run; the rules extractor finds items | `scenarios_create_intake_scope.py` |
| 3. Scope review | Edit, remove, accept and add items; invalid text, status and category refused | `scenarios_create_intake_scope.py` |
| 4. WBS builder | Template generation (never duplicated), manual nodes, invalid durations, reorder, delete, confirm estimates, rules validation | `scenarios_wbs.py` |
| 5a. Team & resources | Add, duplicate (409), invalid hours, edit, remove and re-add; capacity; owners on every package | `scenarios_team_skills.py` |
| 5b. Required skills | Add, duplicate, rename (and merge), Required / Optional, remove | `scenarios_team_skills.py` |
| 5c. Dependencies | Add; self, duplicate, cycle and over-long lag refused; edit, remove | `scenarios_dependencies_constraints.py` |
| 5d. Constraints and questions | Out-of-range limits refused; Must hold / Can flex saved; the optimizer inputs become ready | `scenarios_dependencies_constraints.py` |
| 6. Optimization and Plan v1 | Solve (or priced remedies), schedule complete, real cost, Can-flex cap, PDF and MS Project export, apply once, versions, overview, notification | `scenarios_optimize_apply.py` |
| 7a. After Plan v1 | Inputs stay editable and step 6 notices the change; a re-run aims for the plan in force's date | `scenarios_round2_main_project.py` |
| 7b–7d. Mid-project changes | Hours down and up, a specialist in, a person out, meeting notes, deadlines earlier and later, budget cut, scope growth, progress. Each goes record → answer → re-plan → compare → approve or reject | `scenarios_midproject.py` |
| 7e. Change corner cases | Empty or out-of-range changes, "Nothing changed", decisions out of order, history and versions | `scenarios_corner_cases.py` |
| 8. Over-constrained project | Impossible limits are explained with priced remedies; an empty project can't run or change | `scenarios_corner_cases.py` |
| 8b, 8c. Round-2 projects | Re-plan from today and Done work (D4); a Required skill brings a priced specialist (D6) | `scenarios_round2_small_projects.py` |
| 9. Access control and AI fallbacks | No sign-in → 401; another workspace's user sees nothing (RLS); chat without a key → 503; draft reader on the rules; video refused | `scenarios_corner_cases.py` |
| 10. Token guard | Claude calls the API made during the run (must be 0) | `run_api_suite.py` |

### The product owner's round-2 decisions

Each decision has scenarios that fail on the old behaviour:

| Decision | Covered by |
|---|---|
| D1: worker type. Employees take their rate from the company record; contractors must give one. | 5e |
| D2: when the date slips, the PM is told and offered priced ways to keep it, and each option delivers its date. | 7a |
| D3: no plan lock. Steps 3–5 stay editable after Plan v1; direct edits create no new version. | 7a |
| D4: re-plans start today, and finished work stays put. | 8b |
| D5: skill levels change task length. | Backend unit tests (`backend/tests`), not this suite |
| D6: Required skills and the specialist answer reach the optimizer. | 8c |
| D7: UI fixes (dismiss failed changes, input checks, dates, disabled-button hints). | Not API-testable |

## Run it locally

You need the local Supabase stack (`supabase start`, Auth on `:54321`) and a second API
process **without** a Claude key on `:58001`. That way, the dev API on `:8000` can keep its
key. Log the second API to a file so the token guard can count its Claude calls:

```bash
mkdir -p tests/e2e/api/reports
(cd backend && ANTHROPIC_API_KEY= uv run uvicorn app.main:app --port 58001) \
  > tests/e2e/api/reports/api-under-test.log 2>&1 &

export NIRNAIN_ANON_KEY='<the anon / publishable key from `supabase status`>'
python3.12 tests/e2e/api/run_api_suite.py --fresh-workspace \
  --qa-api-log tests/e2e/api/reports/api-under-test.log
```

A run takes about 3 minutes. It prints the totals and the report path:

```text
PASS 236 · FAIL 1 · BLOCKED 0 · INFO 2
Report: …/tests/e2e/api/reports/api-suite-20261114-143546.md
```

### Accounts

- `--fresh-workspace` (recommended) signs up a brand-new QA account, with its own workspace,
  under a random password. Use it every time. People on a project team are workspace
  people: plans applied by earlier runs book them, and the optimizer (rightly) plans around
  those bookings, so results drift in a reused workspace.
- Without it, the suite signs in as `NIRNAIN_QA_EMAIL` (default `qa@nirnain.local`) with
  `NIRNAIN_QA_PASSWORD`.
- The access-control checks need a user from **another** workspace. Set
  `NIRNAIN_OUTSIDER_EMAIL` (default `demo@nirnain.local`) and `NIRNAIN_OUTSIDER_PASSWORD`
  to use an existing account. If no password is set, the suite signs up a throwaway outsider.

Throwaway addresses look like `qa.run.<time>.<random>@nirnain.local`. Change the domain
with `--fresh-email-domain` if your Auth server rejects it.

## Run it against a hosted demo

This works only while the hosted API has **no** `ANTHROPIC_API_KEY`, for example before step
2.4 of [docs/DEPLOY_DEMO.md](../../../docs/DEPLOY_DEMO.md). Keep **Confirm email** off
(step 1.6) so `--fresh-workspace` gets a session. Never point the suite at production.

```bash
export NIRNAIN_API_URL='<YOUR-API-URL>'          # e.g. https://nirnain-api.onrender.com
export NIRNAIN_AUTH_URL='<SUPABASE-URL>'         # https://<PROJECT-REF>.supabase.co
export NIRNAIN_ANON_KEY='<PUBLISHABLE-KEY>'      # never the service-role key
python3.12 tests/e2e/api/run_api_suite.py --fresh-workspace --fresh-email-domain example.com
```

Wake a sleeping free API first (open `<YOUR-API-URL>/health`). The suite can't read a hosted
API's log, so the token guard row is INFO. Search the host's logs for `api.anthropic.com`
instead.

This suite is not in CI. It needs a running stack and an API without a Claude key, and the
hosted demo API has a key once it is set up.

## Settings

Every flag falls back to an environment variable. Passwords are read only from the
environment, so they never appear in the shell history or the process list.

| Flag | Environment variable | Default |
|---|---|---|
| `--api-url` | `NIRNAIN_API_URL` | `http://127.0.0.1:58001` (without `/api/v1`) |
| `--auth-url` | `NIRNAIN_AUTH_URL` | `http://localhost:54321` (serves `/auth/v1`) |
| `--anon-key` | `NIRNAIN_ANON_KEY` | required. The anon or publishable key, never the service-role key. |
| `--qa-email` | `NIRNAIN_QA_EMAIL` | `qa@nirnain.local` |
| | `NIRNAIN_QA_PASSWORD` | required unless `--fresh-workspace` |
| `--fresh-workspace` | | off |
| `--fresh-email-domain` | `NIRNAIN_FRESH_EMAIL_DOMAIN` | `nirnain.local` |
| | `NIRNAIN_OUTSIDER_EMAIL` | `demo@nirnain.local` |
| | `NIRNAIN_OUTSIDER_PASSWORD` | unset: a throwaway outsider is signed up |
| `--project-name` | `NIRNAIN_QA_PROJECT_NAME` | `QA API - Data Platform Revamp` |
| `--report-dir` | `NIRNAIN_E2E_REPORT_DIR` | `tests/e2e/api/reports/` (git-ignored) |
| `--qa-api-log` | `NIRNAIN_QA_API_LOG` | unset: the token guard is skipped (INFO) |

## Reading the report

Each run writes a new `api-suite-<date>-<time>.md` in the report folder. The file is
rewritten after every scenario, so a run that stops halfway still leaves a readable report.

- **The first line** gives the totals, then the accounts and the API under test.
- **Summary by area** counts each verdict per area.
- **One table per area** lists every scenario: what was expected, what the API answered
  (status and problem detail), and the verdict.

| Verdict | Meaning |
|---|---|
| ✅ PASS | The API behaved as expected. |
| ❌ FAIL | It did not. A row named "Unexpected error in the test itself or the API" is a crash. The area stops there, but the other areas still run. |
| ⛔ BLOCKED | The scenario couldn't run because an earlier step failed (for example, no solved plan to apply). |
| 📝 INFO | An observation without a verdict, such as a zero-day duration, or the token guard without `--qa-api-log`. |

The process exits with **0** when nothing failed, **1** when there is a FAIL or BLOCKED row,
and **2** when the setup failed (bad URL or key, or an account that can't sign in).

**Known failure (B7, low).** The baseline is 236 PASS / 1 FAIL / 2 INFO. The one FAIL is
"Create-from-notes draft without a key (rules)": without Claude, the draft reader misses a
one-line "Project: … Sponsor: …" note. Claude reads it correctly. Any other FAIL is a
regression.

## Files

| File | Purpose |
|---|---|
| `run_api_suite.py` | Entry point: sign-in, area order, crash isolation, token guard, exit code |
| `suite_settings.py` | Flags, environment variables and defaults |
| `suite_client.py` | HTTP client, Supabase Auth sign-in and sign-up, polling |
| `suite_report.py` | Scenario log and markdown report |
| `suite_fixtures.py` | Fake notes, transcript and team; project builders |
| `suite_planning.py` | Optimizer runs and the step-7 change flow |
| `scenarios_*.py` | One module per group of areas (see the table above) |
