# NirnAIn demo seed

`seed_demo.py` fills one demo account so the live app looks like the product owner's approved
mockup. It uses only public APIs: Supabase Auth for the sign-up and the NirnAIn API
(`/api/v1`) for everything else. It never writes to the database and needs nothing but
Python 3.12 (standard library only). Run it again at any time: it keeps what is already
there, or rebuilds it with `--reset`.

## What it creates

The demo user is **Jane Doe** (`jane.doe@nirnain.demo` by default). The projects are created
oldest first, so the walkthrough is the newest and the Dashboard opens on it.

| Project | State | Built from |
|---|---|---|
| Warehouse Build | Planning. Intake, scope, WBS (7 packages), team of 3, skills and limits are done. The 2 step-5 questions are still open. | Mockup portfolio: budget $540K, due Oct 31, 3 risks |
| Portal Redesign | Plan v1 applied, then marked **At Risk** at 38 % | Mockup portfolio: $410K, due Nov 14, design sign-off milestone, 2 risks |
| ERP Migration | Plan v1 applied, **On Track** at 64 % | Mockup portfolio: $820K, due Dec 19, data migration dry run milestone, 1 risk |
| Workday HR Integration | **Draft** (create form only) | Added so the picker shows every stage |
| **Enterprise Data Platform** | All 7 steps, **Plan v1 → Plan v2** | The mockup walkthrough (scope items, WBS, team, plans and views) |

The Enterprise Data Platform walkthrough:

1. **Create:** $375,000, Jan 15 → Sep 30, sponsor Robert Walsh, PM Jane Doe.
2. **Intake:** the kickoff notes are written under the rules extractor's headings. It finds the mockup's 20
   scope items word for word, each with its quote.
3. **Scope review:** the 20 items are accepted. The extractor also files two of the bullets under Constraints,
   and those 2 copies are removed. They show in the "Removed" group.
4. **WBS:** 5 deliverables and 15 work packages. Weeks are converted to working days. Owners and skill tags
   are set, the estimates are confirmed, and **Validate** is run.
5. **Constraints:**
   - Team: Sara Kim, Mark Chen, Tom Rivera and Lisa Park are employees at the standard rate. James Wu is a
     contractor at $140/h.
   - Skills: 6 skills, 5 of them Required.
   - Dependencies: the mockup's 5 plus 10 that order the phases.
   - Limits: budget Must hold, $280K labor and $95K infra caps, 0 % reserve.
   - Questions: an outside SAP specialist is allowed at $175/h (confirmed), and Sep 30 is a hard deadline.
     The two phase milestones are soft.
6. **Optimize and apply Plan v1:** the SAP specialist takes 1.1.2.
7. **Steering Committee notes:** "Tom Rivera can give this project only 24 hours a week". The date slips, so
   the cheapest option that keeps it (a contractor or overtime) is chosen and approved as **Plan v2**.

Dates: the optimizer plans from today, so the mockup's 2025 becomes the first year whose Jan 15 is still ahead
(2027 when run in late 2026). Month and day stay as in the mockup. Override this with `--year`.

## Settings

Every flag falls back to an environment variable. The password is read only from the environment, so it never
appears in the shell history or the process list.

| Flag | Environment variable | Default |
|---|---|---|
| `--auth-url` / `--supabase-url` | `NIRNAIN_AUTH_URL` | `http://localhost:54321` (the URL that serves `/auth/v1`) |
| `--api-url` | `NIRNAIN_API_URL` | `http://127.0.0.1:58001` (without `/api/v1`) |
| `--anon-key` / `--publishable-key` | `NIRNAIN_ANON_KEY` | required. Use the public anon or publishable key, never the service-role key. |
| | `DEMO_PASSWORD` | required. GoTrue needs lower case, upper case and a digit. |
| `--email` | `DEMO_EMAIL` | `jane.doe@nirnain.demo` |
| `--full-name` | `DEMO_FULL_NAME` | `Jane Doe` |
| `--ai-calls rules\|skip` | `DEMO_AI_CALLS` | required, see below |
| `--year` | `DEMO_YEAR` | first year whose Jan 15 is ahead |
| `--reset` | | off. Deletes and rebuilds demo projects that already exist (by name). |

The connection settings are the same `NIRNAIN_*` variables the API end-to-end suite uses
([tests/e2e/api/README.md](../../tests/e2e/api/README.md)). The script refuses the shared test accounts
`demo@nirnain.local` and `qa@nirnain.local`.

### Zero Claude tokens: `--ai-calls`

Only three of the calls can reach Claude: intake, WBS validation and step-7 meeting notes. They do so only when
the API has `ANTHROPIC_API_KEY` set. Pick one mode:

- `rules`: use this only when the API runs **without** `ANTHROPIC_API_KEY`. The three calls then use the
  API's built-in rules. This gives the full mockup look: scope items with quotes, WBS findings, and a change
  read from the notes. After each call the script checks that the answer came from the rules. If it didn't,
  the script stops.
- `skip`: never calls those three endpoints, so it works against any API. The same scope items are added by hand
  (no quotes, chip "Yours"), validation is skipped, and the step-7 change is entered as a direct edit
  (Tom Rivera → 24 h/week).

Optimization, apply and approve never call Claude.

## Run it locally

You need the local Supabase stack (`supabase start`, Auth on `:54321`) and an API without a Claude key on
`:58001`, as for the end-to-end suite:

```bash
(cd backend && ANTHROPIC_API_KEY= uv run uvicorn app.main:app --port 58001) &

export NIRNAIN_ANON_KEY='<the anon / publishable key from `supabase status`>'
export DEMO_PASSWORD='<a password you choose>'
python3.12 scripts/seed_demo/seed_demo.py --ai-calls rules     # add --reset to rebuild everything
```

A full run takes under a minute.

## Run it against the hosted demo (Render + Supabase)

Use the placeholders from [docs/DEPLOY_DEMO.md](../../docs/DEPLOY_DEMO.md). Before you run the script:

- Keep **Confirm email** off (step 1.6), so the sign-up returns a session.
- Run `supabase db push` first.
- The script wakes the Render API itself (it waits for `/health` for up to 3 minutes).

```bash
export NIRNAIN_AUTH_URL='<SUPABASE-URL>'          # https://<PROJECT-REF>.supabase.co
export NIRNAIN_ANON_KEY='<PUBLISHABLE-KEY>'       # sb_publishable_…
export NIRNAIN_API_URL='<YOUR-API-URL>'           # https://nirnain-api.onrender.com
export DEMO_PASSWORD='<a password you choose>'
python3.12 scripts/seed_demo/seed_demo.py --ai-calls skip     # the API has ANTHROPIC_API_KEY (or might have)
python3.12 scripts/seed_demo/seed_demo.py --ai-calls rules    # only while the API has NO ANTHROPIC_API_KEY
```

For the full look, seed before you add `ANTHROPIC_API_KEY` (step 2.4) and use `--ai-calls rules`. Otherwise use
`skip`. If **Confirm email** is on, create the user first in Supabase → Authentication → Add user (auto-confirm)
with the same email and password. The script then signs in instead of signing up.

The script prints one summary line per project: name, code, status, plan versions and id. It exits with 1 and a
`FAILED: <step>: <what the API answered>` line if any step fails. A failed run can leave a half-built project.
Run again with `--reset`.

To check the result, sign in as the demo user and compare the screens with the mockup. A screenshot script is
not included, because it needs Playwright and this folder stays standard-library only.

## What the app cannot show from the mockup

The seed reports these differences instead of working around them:

- **Workspace name:** it stays "Jane Doe's Workspace". The sign-up trigger names it, and the API has no
  workspace rename (`PATCH /me` changes only the profile).
- **Project codes:** codes are `P-001`, `P-002`, … per workspace, not `PRJ-1042`. Re-seeding with `--reset`
  uses new numbers.
- **PM:** it can only be a workspace member, so every project shows **PM Jane Doe**, not Dev Shah or Maria Lopez.
- **Picker chips:** there is no "sample project" flag or chip. All five projects are real.
- **Scope evidence:** without Claude, every scope item is *Stated*. The mockup's *Inferred* and
  *Not in notes* items (13 / 5 / 2) and their "fix to the quoted version" can't be produced.
- **Spend:** "Spent to date" is not tracked ("Not tracked yet"). The mockup's "$512K spent (62 %)" can't be
  shown.
- **Costs and dates:** costs are people × hours × rate only. Employees use the standard $85/h, because employee
  rates come from a company record that the API can't set. Infrastructure isn't modelled. As a result:
  - Plan v1 costs about $174K and goes live in July, against the mockup's $348,600 and Sep 19.
  - Utilization is lower than the mockup's 90 %.
- **Risk levels:** risks the PM records are always *Medium*. Only overbooked people raise *High* plan risks.
- **Milestone status:** it compares the whole plan's finish with the next milestone. Phase 1 therefore shows
  "At risk", not the mockup's "On track" or "1 late".
- **Owners:** WBS owners "J. Doe" and "External" are not on the mockup's team. Those packages go to team members,
  and the optimizer reassigns work anyway.
- **Version history:** step 7 lists Plan v1 (superseded) and Plan v2 (in force), but not where v2 came from
  ("From the Steering Committee notes"). An approved change isn't listed again after a reload.

## Files

| File | Purpose |
|---|---|
| `seed_demo.py` | Entry point: command line, sign-in, workspace, idempotency, summary |
| `seed_client.py` | GoTrue sign-up / sign-in / refresh, `/api/v1` client, polling |
| `seed_flows.py` | Steps 1–3 for any project and the per-project orchestration |
| `seed_resources.py` | Steps 4–5: WBS, team, skills, dependencies, validation, limits |
| `seed_planning.py` | Steps 6–7: optimize, apply, record a change, choose an option, approve |
| `seed_model.py` | Data shapes and the demo calendar |
| `seed_walkthrough_data.py` | Enterprise Data Platform (mockup walkthrough) |
| `seed_portfolio_data.py` | Warehouse Build, the Workday draft, and the portfolio order |
| `seed_portfolio_applied_data.py` | Portal Redesign and ERP Migration (Plan v1 applied) |
