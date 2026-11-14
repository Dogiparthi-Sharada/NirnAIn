# Tests

Where every test in the repository lives, and the one command that runs it. Commands run from the
repository root.

| Suite | Where | What it proves | Command | In CI |
|---|---|---|---|---|
| Backend unit and API tests (pytest) | [`backend/tests/`](../backend/tests) | Domain rules, the optimizer, AI fallbacks and every endpoint, with fakes instead of a database | `cd backend && uv run pytest` | Yes, `api` job (with ruff and mypy) |
| Database tests (pgTAP) | [`supabase/tests/database/`](../supabase/tests/database) | Migrations apply, and RLS keeps every workspace isolated; triggers and constraints hold | `supabase start && supabase test db` | Yes, `db` job |
| API end-to-end scenarios | [`tests/e2e/api/`](e2e/api/README.md) | ~240 business scenarios (steps 1–7, mid-project changes, round-2 decisions, corner cases, access control) against a running API without a Claude key | `python3.12 tests/e2e/api/run_api_suite.py --fresh-workspace` | No, it needs a running stack |
| Demo-data seed (smoke check) | [`scripts/seed_demo/`](../scripts/seed_demo/README.md) | The full 7-step flow, from sign-up to Plan v2, works end to end through the public API | `python3.12 scripts/seed_demo/seed_demo.py --ai-calls rules` | No, it needs a running stack |
| Frontend | [`src/`](../src) | No unit tests yet. Type checking and the production build catch breakage. | `pnpm exec tsc --noEmit -p . && pnpm build` | Yes, `web` job |

## Before you run them

- **pytest** needs only `cd backend && uv sync`.
- **pgTAP** needs Docker for the local Supabase stack. Without Docker, rely on CI: every PR runs the same
  checks.
- **The end-to-end suite and the seed** need the local Supabase stack and an API started **without**
  `ANTHROPIC_API_KEY` on `:58001`, so they spend no Claude tokens. Both read `NIRNAIN_ANON_KEY` (the anon or
  publishable key from `supabase status`); the seed also needs `DEMO_PASSWORD`. They use only the Python
  standard library. Their READMEs give the full setup, the hosted-demo variant and how to read the results.

Never run the end-to-end suite or the seed against production: they create accounts and projects.
