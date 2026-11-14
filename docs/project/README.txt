NirnAIn – AI-Assisted Project Planning Platform
NirnAIn: project documentation (read me first)

Date: November 14, 2026. Author: Sharada Dogiparthi (product owner). Prepared with the NirnAIn build team.

What NirnAIn is

NirnAIn helps a project manager (PM) turn raw meeting notes into a plan that can actually be delivered. AI (Claude, optional) reads notes and documents and pulls out the scope. The PM reviews it, builds a Work Breakdown Structure (WBS) and sets the limits: budget, deadline, team hours and skills. An optimizer (Google OR-Tools CP-SAT) then produces a schedule and staffing plan that respects those limits. When something changes mid-project, NirnAIn re-plans with as little disruption as possible and keeps every applied plan as a version (Plan v1, v2, and so on).

Every AI feature has a rule-based fallback, so the whole app works without an AI key, and then uses 0 Claude tokens.

The seven-step workflow

+----------------+     +----------------+     +----------------+     +----------------+
| 1 Create       |     | 2 AI Project   |     | 3 Scope        |     | 4 WBS          |
|   Project      |---->|   Intake       |---->|   Review       |---->|   Builder      |
+----------------+     +----------------+     +----------------+     +----------------+
                                                                             |
        +--------------------------------------------------------------------+
        v
+----------------+     +----------------+     +----------------------+
| 5 Constraints  |     | 6 Optimization |     | 7 Changes &          |
|                |---->|   Results      |---->|   Versions (v1, v2)  |
+----------------+     +----------------+     +----------------------+

1. Create Project: a form, or upload or paste notes to pre-fill it.
2. AI Project Intake: notes, PDF, Word and meeting transcripts (.vtt, .srt, .txt).
3. Scope Review: accept, edit or remove each extracted item; every item shows the quote it came from.
4. WBS Builder: deliverables and work packages, durations, owners, skill tags and AI validation.
5. Constraints: the team and their hours, skills, dependencies, budget and deadline, each set to "Must hold" or "Can flex".
6. Optimization Results: check the inputs, run the solver, see the plan, its cost and its date, and apply it as Plan vN.
7. Changes & Versions: new meeting notes, edits or progress; compare Plan vN+1 with vN; approve or reject.

A Dashboard shows each project's status, budget, milestones and risks.

Architecture in one picture

                              sign in, refresh (publishable key)
       +--------------------------------------------------------------------------------+
       |                                                                                |
       |                  RENDER (free tiers)                           SUPABASE (new free project)
       |                                                                                |
+------+------+          +------------------------------+                               |
| Browser     | loads    | Static site                  |                               |
|             |--------->| React 19 + Vite build        |               +---------------v----------------+
| project     | the app  | rewrite /* -> /index.html    |               | Auth                           |
| manager,    |          +------------------------------+               | email + password               |
| React app,  |                                                 +------>| JWT signing keys (JWKS)        |
| supabase-js |          +------------------------------+       |       +--------------------------------+
| for sign-in | /api/v1  | Web service (Docker)         |       | verify token (JWKS)
|             |--------->| FastAPI, Python 3.12         |-------+
|             | + Bearer | optimizer: OR-Tools CP-SAT   |
|             | token    | AI intake and WBS jobs       | SQL as        +--------------------------------+
|             |          | rule-based AI fallbacks      |-------------->| Postgres 17                    |
+-------------+          +------------------------------+ authenticated | 25 tables, RLS on every one    |
                                        :                 + the claims  | 17 migrations, pgTAP tests     |
                                        :                               +--------------------------------+
                                        : only with ANTHROPIC_API_KEY
                                        :
                                        v
                         +------------------------------+
                         | Claude API (optional)        |
                         +------------------------------+

The stack

• Website – Technology: React 19, Vite, Tailwind v4, TypeScript, TanStack Query. Folder: src/.
• API – Technology: Python 3.12, FastAPI, SQLAlchemy 2, OR-Tools CP-SAT. Folder: backend/.
• Database – Technology: Supabase: Postgres 17, Auth, Row Level Security. Folder: supabase/.
• AI – Technology: Anthropic Claude (optional; ANTHROPIC_API_KEY on the API only). Folder: backend/app/ai/.
• Hosting – Technology: Render static site + Render web service + Supabase (guide: DEPLOY_DEMO.txt). Folder: Dockerfile.web, backend/Dockerfile.
• CI – Technology: GitHub Actions. Folder: .github/workflows/ci.yml.

The old Railway setup is retired.

How to run it locally (developers)

You need Node.js 22 with pnpm, uv (Python 3.12), and a Supabase project, or a local Supabase started with Docker (supabase start).

Website:

    pnpm install
    cp .env.example .env.local
    pnpm dev

Fill in the Supabase URL, the publishable key and the API URL in .env.local. The website then runs on http://localhost:8443.

API:

    cd backend
    uv sync
    cp .env.example .env
    uv run uvicorn app.main:app --reload --port 8000

Fill in DATABASE_URL, SUPABASE_URL and CORS_ORIGINS in backend/.env.

Database tables: the 17 files in supabase/migrations/, applied with the Supabase CLI (see supabase/README.md and step 1.5 of DEPLOY_DEMO.txt).

Checks before a pull request:

    pnpm exec tsc --noEmit -p . && pnpm build
    cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest

Local demo logins (the team's demo machine only)

On the build machine the demo runs on a Docker-free local stand-in for Supabase, served at http://localhost:54321. These accounts exist only there; they are not accounts on any hosted site.

The demo logins:

• jane.doe@nirnain.demo – Password: JaneDemo2026! What it shows: Jane Doe, the seeded walkthrough: Enterprise Data Platform with Plan v1 and Plan v2, plus ERP Migration, Portal Redesign, Warehouse Build and a draft (Workday HR Integration).
• demo@nirnain.local – Password: DemoPass123! What it shows: The shared test account used during development.

On a hosted demo, create your own account (sign up), or load the same demo data with the seed script (DEPLOY_DEMO.txt, Part 6).

Where each document is

All files are in docs/project/ in the repository. Each document comes as a Word file (.docx) and a plain-text file (.txt) with the same content.

The documents:

• Read me first – Files: README.docx, README.txt. What it covers: This overview.
• Final project report – Files: FINAL_PROJECT_REPORT.docx, FINAL_PROJECT_REPORT.txt. What it covers: What was delivered, quality, risks and next steps.
• Architecture and design decisions – Files: ARCHITECTURE_AND_DESIGN_DECISIONS.docx, ARCHITECTURE_AND_DESIGN_DECISIONS.txt. What it covers: How it works, and the decision log.
• Lessons learned – Files: LESSONS_LEARNED.docx, LESSONS_LEARNED.txt. What it covers: What worked, what didn't, what we'd do differently.
• Hosting quick start – Files: HOSTING_QUICKSTART.docx, HOSTING_QUICKSTART.txt. What it covers: Start hosting today: a 15-step checklist.
• Full hosting guide – Files: DEPLOY_DEMO.docx, DEPLOY_DEMO.txt. What it covers: Every hosting step in detail (also docs/DEPLOY_DEMO.md).
• GitHub upload steps – Files: GITHUB_UPLOAD_STEPS.docx, GITHUB_UPLOAD_STEPS.txt. What it covers: Put this work on GitHub yourself.
• Presentation – Files: NirnAIn_Project_Overview.pptx. What it covers: 18 slides with speaker notes.

The team rules and the original architecture are in AGENTS.md and plan.md at the root of the repository. The API and database guides are backend/README.md and supabase/README.md.

Known limitations

Not built, by product-owner decision: effective-dated availability, leave, holidays, actual hours and estimate at completion, fixed costs, Gurobi, an "as-of" clock, sub-day scheduling and audio transcription. MP4 and other video uploads are refused with a note to upload the transcript instead.

Not present: inviting people and assigning roles in the app, an audit view, a configurable optimizer objective, and renaming a workspace.

Open low-priority items:

• B7: without Claude, "Project: … Sponsor: …" on one line is not read when creating a project from notes (Claude handles it).
• A plan saved before a solver-logic change looks "out of date" once after a deploy: run the optimizer again once.
• Small screens (390 px wide or 200% zoom) are cramped.
• A half-filled Create Project form is not kept when you navigate away.
• Costs are people × hours × rate only (infrastructure isn't modelled), and "spent to date" is not tracked yet.
• The free Render API sleeps after 15 minutes without requests; the first request then takes about a minute, so open the API's /health address before a demo.
