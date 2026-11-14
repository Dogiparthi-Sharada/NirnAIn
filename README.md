# NirnAIn — AI-Assisted Project Planning Platform

Plan projects intelligently: AI turns raw meeting notes and documents into structured scope, builds a Work Breakdown Structure, and an optimization engine produces a feasible schedule and resource plan under real budget, deadline, capacity and skill constraints.

**Flow:** Create Project → AI Intake → Scope Review → WBS Builder → Constraints → Optimization Results

**Live:** the hosted demo (website and API on Render, database on Supabase) is set up with [docs/DEPLOY_DEMO.md](docs/DEPLOY_DEMO.md). Its URLs are the ones you choose there. The old Railway URLs are retired.

## Architecture

| Layer | Tech | Folder |
|-------|------|--------|
| Web app | React 19 · Vite · Tailwind v4 · TypeScript | `src/` |
| API | Python 3.12 · FastAPI · OR-Tools | `backend/` |
| Database & Auth | Supabase (Postgres, Auth, Storage, Row Level Security) | `supabase/` |
| Hosting | Railway (auto-deploy from `main`) | `Dockerfile.web`, `backend/Dockerfile` |
| CI/CD | GitHub Actions | `.github/workflows/ci.yml` |

Design and roadmap: [plan.md](plan.md). Team and coding rules: [AGENTS.md](AGENTS.md). Database guide: [supabase/README.md](supabase/README.md).

## Quick start

```bash
# Web
pnpm install
cp .env.example .env.local        # fill in Supabase URL + publishable key, API URL
pnpm dev                          # http://localhost:8443

# API
cd backend
uv sync
cp .env.example .env              # DATABASE_URL, SUPABASE_URL, CORS_ORIGINS
uv run uvicorn app.main:app --reload --port 8000
```

## Host the demo for free

[docs/DEPLOY_DEMO.md](docs/DEPLOY_DEMO.md) walks through hosting the demo step by step on free tiers: a new Supabase
project (database and sign-in), the API as a Render free web service (Docker), and the website as a Render static site.

## Project documentation

The hand-over package for the NirnAIn demo is in [docs/project/](docs/project/). Every document comes as Word (.docx) and plain text (.txt):

| Document | Word | Text |
|---|---|---|
| Read me first | [README.docx](docs/project/README.docx) | [README.txt](docs/project/README.txt) |
| Final project report | [FINAL_PROJECT_REPORT.docx](docs/project/FINAL_PROJECT_REPORT.docx) | [FINAL_PROJECT_REPORT.txt](docs/project/FINAL_PROJECT_REPORT.txt) |
| Architecture and design decisions | [ARCHITECTURE_AND_DESIGN_DECISIONS.docx](docs/project/ARCHITECTURE_AND_DESIGN_DECISIONS.docx) | [ARCHITECTURE_AND_DESIGN_DECISIONS.txt](docs/project/ARCHITECTURE_AND_DESIGN_DECISIONS.txt) |
| Lessons learned | [LESSONS_LEARNED.docx](docs/project/LESSONS_LEARNED.docx) | [LESSONS_LEARNED.txt](docs/project/LESSONS_LEARNED.txt) |
| Hosting quick start | [HOSTING_QUICKSTART.docx](docs/project/HOSTING_QUICKSTART.docx) | [HOSTING_QUICKSTART.txt](docs/project/HOSTING_QUICKSTART.txt) |
| Full hosting guide | [DEPLOY_DEMO.docx](docs/project/DEPLOY_DEMO.docx) | [DEPLOY_DEMO.txt](docs/project/DEPLOY_DEMO.txt) |
| GitHub upload steps | [GITHUB_UPLOAD_STEPS.docx](docs/project/GITHUB_UPLOAD_STEPS.docx) | [GITHUB_UPLOAD_STEPS.txt](docs/project/GITHUB_UPLOAD_STEPS.txt) |

Presentation: [NirnAIn_Project_Overview.pptx](docs/project/NirnAIn_Project_Overview.pptx) (18 slides with speaker notes).

## Contributing

1. Branch from `main`: `git switch -c feat/<area>-<thing>`
2. Open a PR. CI must pass, and the area's CODEOWNER must approve.
3. Merging to `main` deploys to production automatically (DB migrations first, then web + api).
