# Project Planning API (backend)

FastAPI service for the AI-Assisted Project Planning Platform. It's a modular
monolith: the planned optimizer (OR-Tools) and AI modules will be added as new
`domain/`, `repositories/` and `api/v1/` modules.

- **Database:** Supabase Postgres. The schema lives in `../supabase/migrations`
  and belongs to the DB team. This service has no migrations and never creates tables.
- **Auth:** Supabase Auth. Clients send `Authorization: Bearer <access token>`.
  Tokens are verified against the project's JWKS (ES256/RS256). HS256 is accepted
  only if `SUPABASE_JWT_SECRET` is set.
- **Authorization:** Postgres RLS. Every user request runs in its own transaction
  with `request.jwt.claims` / `request.jwt.claim.sub` set and `set local role authenticated`,
  so `auth.uid()` and every policy work the same as through the Supabase API
  (`app/core/db.py:get_user_db`). `get_service_db` skips RLS and is only for trusted
  system jobs. User-facing endpoints must never use it.

## Layout

```
app/
  main.py            app factory: middleware, routers, exception handlers
  core/              config, db (engine + RLS sessions), security (JWT), errors (RFC 7807),
                     logging (JSON), middleware (request id, access log, security headers),
                     pagination (keyset cursors)
  api/deps.py        repository providers (tests override these)
  api/v1/            routers: me, orgs, projects, dashboard, notifications, optimization, intake,
                     documents, wbs, planning, ai jobs, assistant, health
  ai/                Claude wrapper (llm.py), prompts and pure AI logic: document text extraction,
                     intake notes / evidence / rule-based fallback, WBS generate/validate, assistant
  optimization/      pure CP-SAT optimizer (model, baseline, solver, explanations)
  jobs/              in-process background workers (optimization, intake, AI jobs)
  domain/            pydantic schemas (camelCase JSON) + Postgres enum mirrors
  repositories/      SQLAlchemy Core queries against the existing tables (tables.py)
tests/               unit + API tests (no database needed)
```

## Run locally

Requires [uv](https://docs.astral.sh/uv/). The Python version is pinned in `.python-version`.

```bash
cd backend
uv sync
cp .env.example .env        # then fill in DATABASE_URL, SUPABASE_URL, CORS_ORIGINS
uv run uvicorn app.main:app --reload --port 8000
```

- Liveness: <http://localhost:8000/health>
- Readiness (checks the DB): <http://localhost:8000/health/ready>
- OpenAPI docs: <http://localhost:8000/api/docs>. They're off in production unless `ENABLE_DOCS=true`.

Without `DATABASE_URL`/`SUPABASE_URL`, the app still starts in development.
`/health` works, and endpoints that need the DB or auth return `503`.

## Test and lint

```bash
uv run pytest -q                       # tests
uv run coverage run -m pytest -q && uv run coverage report
uv run ruff check .                    # lint
uv run ruff format .                   # format (use --check in CI)
uv run mypy app                        # type check (strict)
```

The tests use in-memory fakes for the repositories, a generated EC key with a
mocked JWKS for JWT verification, and a recording connection that checks the
RLS session setup and the generated SQL. They don't cover RLS itself. Test that
against a real Supabase instance, e.g. `supabase start` plus a seeded user.

## API

All endpoints are under `/api/v1` except health. JSON uses camelCase. Errors are
`application/problem+json` (RFC 7807) and include a `requestId`.

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `{status, version}`. No DB access. |
| GET | `/health/ready` | Runs `select 1`. Returns 503 if the DB is unreachable. |
| GET | `/api/v1/me` | Profile plus `organizations[{id,name,slug,role}]` |
| PATCH | `/api/v1/me` | `fullName`, `jobTitle` |
| GET | `/api/v1/orgs/{orgId}/members` | Roster with profile names (PM dropdown) |
| GET | `/api/v1/orgs/{orgId}/projects` | `status` (repeatable), `q`, `limit` (1–100, default 25), `cursor`. Returns `{items, nextCursor}`. |
| POST | `/api/v1/orgs/{orgId}/projects` | Returns 201 plus a `Location` header |
| GET/PATCH/DELETE | `/api/v1/projects/{projectId}` | Partial PATCH. DELETE is a hard delete and returns 204. |
| GET | `/api/v1/orgs/{orgId}/dashboard` | `{totalProjects, onTrack, atRiskOrDelayed, totalBudgetCents, createdThisMonth}` |
| GET | `/api/v1/notifications` | `unread=true/false`, `limit`, `cursor` |
| POST | `/api/v1/notifications/{id}/read` | Idempotent. Returns the notification. |
| GET | `/api/v1/projects/{projectId}/optimization/inputs` | Confirmation sheet: `{rows: [{key, label, value, source, from}], ready, blocking, missing, hash}` |
| POST | `/api/v1/projects/{projectId}/optimization/runs` | Optional body `{remedy?: 'specialist'\|'overtime'\|'move_deadline', overrides?: {budgetTotalCents?, mustFinishBy?, durationDays?, hoursPerWeek?}, changeRequestId?, baselineRunId?}` (stored as `options`, plus `inputsHash` = the inputs sheet's `hash` for the saved inputs). 202 + `OptimizationRun` (`queued`), solved in the background. 403 not a writer, 404 project / change request / baseline run not visible, 409 a run is already queued/running or the change was decided, 422 inputs not ready (`missing`: `workPackages`, `estimates`, `deadline`, `specialist`, `team`). |
| GET | `/api/v1/projects/{projectId}/optimization/runs/latest` | Latest run, or 404 when there are none |
| GET | `/api/v1/projects/{projectId}/optimization/runs/{runId}` | One run |

| POST | `/api/v1/projects/{projectId}/documents` | Multipart `file`. 201 + `SourceDocument`. 403 not a writer, 404 project not visible, 413 over 100 MB, 415 video or unsupported type. |
| GET | `/api/v1/projects/{projectId}/documents` | Uploaded documents, newest first (pasted notes are not listed) |
| DELETE | `/api/v1/documents/{documentId}` | 204. 403 not a writer, 404 not visible. |
| POST | `/api/v1/projects/{projectId}/intake/runs` | `{text}` (may be empty when an upload was read; 422 when there is nothing to analyze). 202 + `IntakeRun`, extracted in the background. 409 a run is active. |
| GET | `/api/v1/projects/{projectId}/intake/runs` | The 20 most recent runs, newest first |
| GET | `/api/v1/projects/{projectId}/intake/runs/latest` · `/{runId}` | One run (404 when none) |
| GET/POST | `/api/v1/projects/{projectId}/scope-items` | List (removed items hidden) / add by hand (accepted, evidence null) |
| PATCH | `/api/v1/scope-items/{itemId}` | `{text?, status?}`. Text equal to `suggestedText` makes the item `stated`. |

Error mapping: RLS violation / `42501` → 403, trigger `P0001` → 422 (with the trigger's
message), `23505` → 409, `23514`/`23503`/`23502`/`22xxx` → 422, missing row → 404,
DB unreachable / pool exhausted / statement timeout → 503.

## Optimizer

`app/optimization/` is a pure OR-Tools CP-SAT package (no DB or FastAPI imports):
`model.py` (input/output + the frozen `input_snapshot` format), `baseline.py`
(forward-pass "before" plan), `solver.py` (model, status mapping, infeasibility
explanation via assumption literals; the module docstring documents the formulation
and its simplifications), `resources.py` (the priced team and contractor profiles),
`workload.py` (work modes, overtime and capacity per person), `explain.py` +
`run_summary.py` (the UI `result` payload and DB rows), `overrides.py` (per-run
what-ifs), `remedies.py` (priced ways to keep the date, `result.remedies`) and
`inputs.py` + `input_sheet.py` (the confirmation sheet).

Every solved run reports `result.summary.targetDate` (the completion date of the plan
in force for a re-plan with `baselineRunId`, else the must-finish-by date) and
`slipDays` (working days after it, 0 when on time). When the plan slips or the run is
infeasible, `result.remedies[]` lists `{id, title, detail, costCents, completionDate}`,
each re-solved: `specialist` (a contractor for skill gaps and any late work, at the
contractor rate or $175/h), `overtime` (the people on the late work up to 25% over
their hours, 1.5x for employees) and `move_deadline` (accept the new date, 0 extra).
Options are offered only when they finish earlier; `costCents` is the extra cost
against this plan (the whole relaxed plan's cost for an infeasible run). "Can flex"
capacity never exceeds 125% of contracted hours.

Inputs (`app/repositories/planning_input.py`): capacity comes from the project team at
the hours they give this project (all active org employees when the project has no
team); the deadline is soft only when the PM answered "soft"; outside specialists only
when the PM answered "yes" (contractor rate, else $175/h), for the work packages needing
skills nobody on the team has. The project's Required skills reach the work packages
tagged with them (`PATCH /wbs/nodes/{nodeId}` `{skills: [name]}`, needed at level 3),
else those whose name or description mention every word of the skill
(`skill_links.py`); a Required skill no package uses is a `needs_you` warning on the
inputs sheet. Unconfirmed AI estimates and unanswered questions that matter block the
run (422). A person's skill level sets their pace on a work package: each level above
its minimum is 10% faster (at most 30%), each level below ("Can flex" skills only) 25%
slower; the hours, and so the duration and cost, scale with it, on top of a
part-timer's stretch (`speed.py`).

`POST .../optimization/runs` loads the plan under the caller's RLS session, freezes it
into `input_snapshot` and commits. A background task then solves it in-process
(`app/jobs/optimizer.py`, `asyncio.to_thread`) and writes the outcome, plus
`planned_assignments` and `recommendations`, on a service connection. On startup,
and before answering 409, runs stuck in queued/running for longer than
`OPTIMIZER_STALE_AFTER_S` are marked failed, because a restart orphans them. Tuning:
`OPTIMIZER_TIME_LIMIT_S`, `OPTIMIZER_WORKERS` (see `.env.example`).

## AI intake

`POST .../documents` keeps only the text of an upload (no object storage yet):
PDF (pypdf), Word `.docx` (paragraphs and table cells), `.txt`/`.md`/`.vtt`/`.srt`
transcripts (WebVTT/SRT headers, cue numbers and timings removed, consecutive
captions of the same speaker merged into one line). The kind comes from the
extension, then the MIME type. Files may be up to 100 MB ("Files can be at most
100 MB."): a larger declared Content-Length is refused before reading, the body is
otherwise counted as it streams in, and the spooled file is parsed in place (text
files: the first 8 MB). Text is capped at 200 000 characters per file. Video
(`.mp4 .mov .m4v .avi .mkv .wmv`, `.webm` unless `audio/webm`, any `video/*` type)
is refused with 415 ("MP4 and other video files are not supported. Upload the
meeting's transcript (.vtt, .srt or .txt) instead."). Audio is stored as a failed
extraction ("Audio transcription is not available yet."), as are scanned or
corrupt files, always with a fixed message. The same size and video rules apply to
`POST /orgs/{orgId}/project-drafts/extract`.
`SourceDocument = {id, projectId, kind: pdf|docx|transcript|audio, title, mimeType,
sizeBytes, extractionStatus: succeeded|failed, extractionError, chars, createdAt}`.

An intake run reads its own pasted notes plus every upload whose text was read
(transcripts, including captions pasted as notes, first reduced to one line per
speaker turn), each as `<document title="…">` inside one `<project_notes>` block (≤ 400 000
characters; longer notes are cut longest-document-first, with a note saying so).
The worker (`app/jobs/intake.py`) writes results on a service connection:

- With `ANTHROPIC_API_KEY`, Claude (`INTAKE_MODEL`) returns each scope item with
  its evidence. A quote is kept only if it really occurs in the notes (ignoring
  case, whitespace and typographic quotes); otherwise the item becomes
  `inferred` with "No exact quote found in the notes." (`app/ai/evidence.py`).
- Without a key, or when the Claude call fails (API error, refusal, invalid
  answer; logged), a rule-based extractor runs (`app/ai/intake_rules.py`, run
  `model = "rules"`, no tokens). Every item is `stated` and quotes the notes
  verbatim. Bullets under OBJECTIVES/GOALS, REQUIREMENTS, DELIVERABLES,
  CONSTRAINTS/BUDGET/TIMELINE, RISKS/CONCERNS and OPEN ITEMS/UNKNOWNS/QUESTIONS
  headings take that category (plus a constraint when they name money or a
  deadline). Everything else, and transcripts always, is read sentence by sentence
  without speaker labels (`app/ai/intake_sentences.py`) and classified by the first
  matching keyword rule (`app/ai/intake_keywords.py`): missing info (don't know, not
  sure, TBD, unclear, open questions), risk, constraint (budget, amounts, deadlines,
  "only N"), objective, deliverable, requirement; sentences matching nothing are
  skipped. Near-duplicates count once and a run keeps at most 60 items, shared
  fairly between the categories.

`ScopeItem` carries `sourceQuote`, `evidence` (`stated` | `inferred` | `flagged` |
null for items added by hand), `evidenceNote`, `suggestedText` (flagged items: the
version the quote supports) and `sourceDocumentId` (the upload the quote is from).

## Deploy (Railway)

The service deploys from `backend/Dockerfile`. It uses a uv builder, then a slim
runtime image that runs as a non-root user.

1. Create a Railway service from this repo. Set **Root Directory** to `/backend`.
   Set the **config file path** to `/backend/railway.json`, because the config
   file path doesn't follow the root directory.
2. Set the variables from `.env.example`: `ENVIRONMENT=production`, `DATABASE_URL`
   (Supavisor **session pooler** URL, IPv4), `DATABASE_SSL=require`, `SUPABASE_URL`,
   `CORS_ORIGINS`, and optionally `SUPABASE_JWT_SECRET` and `LOG_LEVEL`.
   Railway provides `PORT` and `RAILWAY_GIT_COMMIT_SHA`.
3. Railway health-checks `/health`. Only changes under `backend/**` trigger a redeploy.
