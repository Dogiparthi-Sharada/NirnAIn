# Browser regression tests

Scripts that drive the **web app** in Chromium (Playwright for Python) against a running local
stack, for bugs that only show in the browser: what a screen shows while its data is reloading,
and what a screen starts by itself. Each script builds its own projects through the public API,
in a throwaway account, then checks the screens the way a project manager uses them.

> [!IMPORTANT]
> Run them only against an API started **without** `ANTHROPIC_API_KEY` (rules intake and
> template WBS: **0 Claude tokens**). Never run them against production: they create accounts
> and projects.

## What they cover

| Script | Bug | Scenarios | Runs in |
|---|---|---|---|
| `test_stale_step6_inputs.py` | C1: step 6 showed the inputs sheet from before the team was added ("nobody has Maps, React Native, … yet") until a full page refresh. | With the sheet and the team saves slowed down the way the hosted API is: (1) step 6, add the team on step 5, back to step 6 within 30 s; (2) add one more person and click step 6 before the save has answered. Each time step 6 must say "Checking the latest inputs…" with **Approve inputs & run** disabled until the fresh sheet arrives, never present the old "Required skills" row as current, and end on the server's row, ready to approve. | ~20 s |
| `test_wbs_empty_state.py` | C2: the WBS Builder started a generation before any scope was accepted, and later kept showing that old failure. | (a) Intake done, nothing accepted: step 4 says "Accept your scope items first", its button opens Scope Review, and **no** `wbs_generate` job is created. (b) A generation that failed before the intake (created through the API), scope accepted since: the WBS appears by itself, without Try again. (c) A generation that failed with the scope already accepted: Try again is shown and nothing is retried automatically (it would cost tokens on a hosted API with a key). | ~60 s |

Each script fails on the code before its fix:

| Script | Before the fix | After |
|---|---|---|
| `test_stale_step6_inputs.py` | PASS 6 · FAIL 3 (the pre-team sheet shown as current for ~2.5 s; Approve enabled on an out-of-date sheet) | PASS 9 · FAIL 0 |
| `test_wbs_empty_state.py` | PASS 4 · FAIL 8 (a job created without accepted scope; the old failure shown instead of the WBS) | PASS 12 · FAIL 0 |

## Before you run them

1. **The local Supabase stack** (`supabase start`, Auth on `:54321`).
2. **An API without a Claude key on `:58001`**, as for the [API suite](../api/README.md):

   ```bash
   (cd backend && ANTHROPIC_API_KEY= uv run uvicorn app.main:app --port 58001) &
   ```

3. **The web app, pointed at that API.** Either the dev server or the built site works; the
   port is up to you (`NIRNAIN_WEB_URL`):

   ```bash
   export VITE_SUPABASE_URL=http://localhost:54321
   export VITE_SUPABASE_PUBLISHABLE_KEY='<the anon / publishable key>'
   export VITE_API_URL=http://127.0.0.1:58001
   pnpm dev --port 58940 --strictPort
   # or: pnpm build && pnpm preview --port 58940 --strictPort
   ```

4. **Playwright for Python** with Chromium, in a virtual environment outside the repository:

   ```bash
   python3.12 -m venv ~/.venvs/nirnain-browser
   ~/.venvs/nirnain-browser/bin/pip install playwright
   ~/.venvs/nirnain-browser/bin/playwright install chromium
   ```

The scripts start Chromium with web security off, so a dev server on any port can call the
local API and Auth without adding its origin to their CORS lists. They also refuse every API
call the page makes to any host but `NIRNAIN_API_URL`, so a site built for another API (one
with a Claude key, say) can't spend tokens.

## Run them

From the repository root:

```bash
export NIRNAIN_ANON_KEY='<the anon / publishable key>'
~/.venvs/nirnain-browser/bin/python tests/e2e/browser/test_stale_step6_inputs.py
~/.venvs/nirnain-browser/bin/python tests/e2e/browser/test_wbs_empty_state.py
```

Each prints one PASS or FAIL line per check and the totals, and exits with **0** when every
check passed, **1** when one failed and **2** when the setup failed (bad URL or key, or an
account that can't sign in). A failing screen check also saves a screenshot in the report
folder.

## Settings

| Environment variable | Default |
|---|---|
| `NIRNAIN_ANON_KEY` | required. The anon or publishable key, never the service-role key. |
| `NIRNAIN_WEB_URL` | `http://127.0.0.1:58940` (the dev server or built site) |
| `NIRNAIN_API_URL` | `http://127.0.0.1:58001` (without `/api/v1`; the API the web app was started with) |
| `NIRNAIN_AUTH_URL` | `http://localhost:54321` (serves `/auth/v1`) |
| `NIRNAIN_QA_EMAIL`, `NIRNAIN_QA_PASSWORD` | unset: a throwaway account (and workspace) is signed up for each run |
| `NIRNAIN_FRESH_EMAIL_DOMAIN` | `nirnain.local` |
| `NIRNAIN_BROWSER_HEADED` | unset; `1` shows the browser window |
| `NIRNAIN_E2E_REPORT_DIR` | `tests/e2e/browser/reports/` (git-ignored) |

## Files

| File | Purpose |
|---|---|
| `browser_support.py` | Settings, sign-in, project builders (through the API suite's `suite_client.py`), the guarded browser page, the PASS/FAIL log |
| `test_stale_step6_inputs.py` | C1: step 6 shows fresh inputs |
| `test_wbs_empty_state.py` | C2: the WBS Builder's empty state and automatic generation |
