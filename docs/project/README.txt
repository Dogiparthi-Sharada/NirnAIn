NirnAIn - AI-Assisted Project Planning Platform
Project documentation package (demo build, November 14, 2026)
Product owner: Sharada Dogiparthi
=======================================================================

WHAT NIRNAIN IS
---------------
NirnAIn helps a project manager (PM) turn raw meeting notes into a plan
that can actually be delivered. AI (Claude, optional) reads notes and
documents and pulls out the scope. The PM reviews it, builds a Work
Breakdown Structure (WBS) and sets the limits (budget, deadline, team
hours, skills). An optimizer (Google OR-Tools CP-SAT) then produces a
schedule and staffing plan that respects those limits. When something
changes mid-project, NirnAIn re-plans with as little disruption as
possible and keeps every applied plan as a version (Plan v1, v2, ...).

Every AI feature has a rule-based fallback, so the whole app works
without an AI key (it then uses 0 Claude tokens).

THE 7-STEP WORKFLOW
-------------------
  1. Create Project        - a form, or upload/paste notes to pre-fill it
  2. AI Project Intake     - notes, PDF, Word, transcripts (.vtt/.srt/.txt)
  3. Scope Review          - accept, edit or remove each extracted item;
                             every item shows the quote it came from
  4. WBS Builder           - deliverables and work packages, durations,
                             owners, skill tags, AI validation
  5. Constraints           - team and hours, skills, dependencies,
                             budget/deadline, "Must hold" or "Can flex"
  6. Optimization Results  - check the inputs, run the solver, see the
                             plan, its cost and date; apply it as Plan vN
  7. Changes & Versions    - new meeting notes, edits or progress;
                             compare Plan vN+1 with vN; approve or reject

A Dashboard shows each project's status, budget, milestones and risks.

THE STACK
---------
  Website   React 19, Vite, Tailwind v4, TypeScript, TanStack Query (src/)
  API       Python 3.12, FastAPI, SQLAlchemy 2, OR-Tools CP-SAT (backend/)
  Database  Supabase: Postgres 17, Auth, Row Level Security (supabase/)
  AI        Anthropic Claude (optional; ANTHROPIC_API_KEY on the API only)
  Hosting   Render static site + Render web service + Supabase
            (docs/DEPLOY_DEMO.md; the old Railway setup is retired)
  CI        GitHub Actions (.github/workflows/ci.yml)

HOW TO RUN IT LOCALLY (developers)
----------------------------------
You need Node.js 22 with pnpm, uv (Python 3.12), and a Supabase project
(or a local Supabase started with Docker: `supabase start`).

  Website:
    pnpm install
    cp .env.example .env.local     # Supabase URL + publishable key, API URL
    pnpm dev                       # http://localhost:8443

  API:
    cd backend
    uv sync
    cp .env.example .env           # DATABASE_URL, SUPABASE_URL, CORS_ORIGINS
    uv run uvicorn app.main:app --reload --port 8000

  Database tables: the 17 files in supabase/migrations/, applied with the
  Supabase CLI (supabase/README.md; docs/DEPLOY_DEMO.md step 1.5).

  Checks before a pull request:
    pnpm exec tsc --noEmit -p . && pnpm build
    cd backend && uv run ruff check . && uv run ruff format --check . \
      && uv run mypy app && uv run pytest

LOCAL DEMO LOGINS (the team's demo machine only)
------------------------------------------------
On the build machine the demo runs on a Docker-free local stand-in for
Supabase, served at http://localhost:54321. These accounts exist only
there. They are NOT accounts on any hosted site.

  jane.doe@nirnain.demo / JaneDemo2026!
      Jane Doe, the seeded walkthrough: Enterprise Data Platform with
      Plan v1 -> Plan v2, plus ERP Migration, Portal Redesign, Warehouse
      Build and a draft (Workday HR Integration).
  demo@nirnain.local / DemoPass123!
      The shared test account used during development.

On a hosted demo, create your own account (sign-up), or load the same
demo data with the seed script (docs/DEPLOY_DEMO.md, Part 6).

WHERE EACH DOCUMENT IS
----------------------
  docs/project/README.txt                           this file
  docs/project/FINAL_PROJECT_REPORT.md              what was delivered,
                                                    quality, risks, next steps
  docs/project/ARCHITECTURE_AND_DESIGN_DECISIONS.md how it works + decision log
  docs/project/LESSONS_LEARNED.md                   what worked, what didn't
  docs/project/NirnAIn_Project_Overview.pptx        the presentation
                                                    (with speaker notes)
  docs/project/HOSTING_QUICKSTART.md                start hosting today
  docs/project/GITHUB_UPLOAD_STEPS.md               put this work on GitHub
  docs/DEPLOY_DEMO.md                               full hosting guide
  AGENTS.md, plan.md                                team rules, architecture
  backend/README.md, supabase/README.md             API and database guides

KNOWN LIMITATIONS
-----------------
  Not built, by product-owner decision: effective-dated availability,
  leave, holidays, actual hours / estimate at completion, fixed costs,
  Gurobi, an "as-of" clock, sub-day scheduling, audio transcription.
  MP4/video uploads are refused with a note (upload the transcript).

  Not present: inviting people and assigning roles in the app, an audit
  view, a configurable optimizer objective, renaming a workspace.

  Open low-priority items:
  - B7: without Claude, "Project: ... Sponsor: ..." on one line is not
    read when creating a project from notes (Claude handles it).
  - A plan saved before a solver-logic change looks "out of date" once
    after a deploy: run the optimizer again once.
  - Small screens (390 px wide or 200% zoom) are cramped.
  - A half-filled Create Project form is not kept when you navigate away.
  - Costs are people x hours x rate only (infrastructure isn't modelled);
    "spent to date" is not tracked yet.
  - The free Render API sleeps after 15 minutes idle; the first request
    then takes about a minute (open <API>/health before a demo).
