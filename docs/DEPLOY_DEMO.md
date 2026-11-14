# Host the NirnAIn demo for free

This guide takes you from zero to a working public demo, one step at a time:

| Part | Runs on | Free tier | What it is |
|------|---------|-----------|------------|
| Database + sign-in | **Supabase** (new free project) | 2 free projects; paused after 1 week without activity | Postgres, Auth |
| API | **Render** free web service (Docker) | 750 instance hours/month; sleeps after 15 min idle | `backend/` (FastAPI) |
| Website | **Render** static site | Free; served from Render's CDN, never sleeps | `src/` (React), built by Render |

Railway and the old Supabase project are no longer used. Nothing in this guide touches them. No code changes are needed.

Plan for about an hour. Do the parts in this order: Supabase, the API, the website, back to Supabase for the sign-in URLs, then the first login.

---

## Before you start

### Accounts (all free)

- GitHub access to this repository. Render builds both the API and the website straight from GitHub.
- Supabase: <https://supabase.com/dashboard/sign-up>
- Render: <https://dashboard.render.com/register>. Sign up with GitHub, which makes step 2.1 easier.

### Tools on your computer

You only need these to create the database tables (step 1.5). Render builds everything else.

- **Git**: <https://git-scm.com/downloads>
- **Node.js 22 LTS**: <https://nodejs.org/>. It includes `npx`, which runs the Supabase CLI.

Check it worked: `node --version` prints `v22.…`.

### Get the code

```bash
git clone https://github.com/<ORG>/AI-Assisted-Project-Planning-Platform.git
cd AI-Assisted-Project-Planning-Platform
git switch <DEMO-BRANCH>        # the branch that holds the demo, e.g. demo/mockup (or main once merged)
```

Run every later command from this folder.

### Your values worksheet

Keep these in a password manager or a private note. Never commit them, and never paste them into an issue or chat.

| Placeholder | Example | You get it in step |
|-------------|---------|--------------------|
| `<PROJECT-REF>` | `abcdefghijklmnopqrst` | 1.1 |
| `<DB-PASSWORD>` | (letters and digits only) | 1.1 |
| `<SUPABASE-URL>` | `https://<PROJECT-REF>.supabase.co` | 1.2 |
| `<PUBLISHABLE-KEY>` | `sb_publishable_…` | 1.2 |
| `<SESSION-POOLER-URL>` | `postgresql://postgres.<PROJECT-REF>:<DB-PASSWORD>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres` | 1.4 |
| `<API-NAME>`, `<WEB-NAME>` | `nirnain-api`, `nirnain-web` | decide now |
| `<YOUR-API-URL>` | `https://nirnain-api.onrender.com` | 2.2 |
| `<YOUR-WEB-URL>` | `https://nirnain-web.onrender.com` | 3.1 |

Decide `<API-NAME>` and `<WEB-NAME>` now, using lowercase letters, digits and `-` only. Render turns a service name
into its address, `https://<name>.onrender.com`. It adds a short suffix only if someone else already uses the name.
You need the website's address for the API in step 2.2, before the website exists.

---

## Part 1: Supabase (database and sign-in)

### 1.1 Create the project

1. Go to <https://supabase.com/dashboard> and click **New project**. Choose your organization.
2. **Project name**: `nirnain-demo`.
3. **Database password**: click **Generate a password**, or type a long password that uses **only letters and digits**.
   Symbols such as `@ # / ? %` would have to be URL-encoded in step 1.4. Save it as `<DB-PASSWORD>`.
4. **Region**: pick one that Render also offers, so the API is close to the database:

   | Supabase region | Matching Render region (step 2.2) |
   |-----------------|-----------------------------------|
   | Central EU (Frankfurt) | Frankfurt |
   | East US (North Virginia) | Virginia |
   | East US (Ohio) | Ohio |
   | West US (Oregon) | Oregon |
   | Southeast Asia (Singapore) | Singapore |

5. Leave the other options at their defaults and click **Create new project**. Setup takes about 2 minutes.
6. The address bar now reads `https://supabase.com/dashboard/project/<PROJECT-REF>`. Copy that id as `<PROJECT-REF>`.

✅ **Check it worked:** the project's home page loads without a "Setting up project" banner.

### 1.2 Copy the project URL and the publishable key

1. Click **Project Settings** (gear icon) → **API Keys**.
2. Copy the **Publishable key** (`sb_publishable_…`) as `<PUBLISHABLE-KEY>`. It is designed to be public: it ends up in the website, and the database's Row Level Security protects the data.
3. `<SUPABASE-URL>` is `https://<PROJECT-REF>.supabase.co`, with no trailing slash. (It's also shown in the **Connect** dialog.)

Don't copy the **secret** key (`sb_secret_…`) or the legacy `service_role` key. This app doesn't need them, and they must never leave Supabase.

✅ **Check it worked:** the key starts with `sb_publishable_`.

### 1.3 Check how sign-in tokens are signed

The API verifies each user's sign-in token with the project's public signing keys, published at
`<SUPABASE-URL>/auth/v1/.well-known/jwks.json`. It checks ES256/RS256 tokens this way. It accepts the old
shared-secret (HS256) tokens only if you give it `SUPABASE_JWT_SECRET`.

1. Open **Project Settings** → **JWT Keys**.
2. Look at **Current key**:
   - **ECC (P-256)** or **RSA**: you're done. Leave `SUPABASE_JWT_SECRET` out in step 2.2.
   - **Legacy JWT secret** (HS256, shared secret): the project has no users yet, so it's safe to switch. Click **Migrate JWT secret**, then **Rotate keys**.
     The current key becomes ECC (P-256). (If you'd rather not, copy the legacy JWT secret and set it as `SUPABASE_JWT_SECRET` in step 2.2.)

✅ **Check it worked:** **Current key** shows ECC (P-256) or RSA (or you set the legacy secret aside for step 2.2).
Then open `<SUPABASE-URL>/auth/v1/.well-known/jwks.json` in your browser. You should see `{"keys":[{… "alg":"ES256" …}]}`.
If the browser says "No API key found", add `?apikey=<PUBLISHABLE-KEY>` to the address.
An empty list (`{"keys":[]}`) means the project still signs with the legacy secret.

### 1.4 Copy the database connection string (Session pooler)

1. Click **Connect** at the top of the project page.
2. Choose **Session pooler** and copy the URI. It looks like this:

   ```text
   postgresql://postgres.<PROJECT-REF>:[YOUR-PASSWORD]@aws-<N>-<region>.pooler.supabase.com:5432/postgres
   ```

3. Replace `[YOUR-PASSWORD]` (brackets included) with `<DB-PASSWORD>`. Save the result as `<SESSION-POOLER-URL>`.
   Copy the host from the dialog; don't type it yourself, because the `aws-<N>` part varies.

Why the session pooler?

- On the free plan, the **direct connection** (`db.<PROJECT-REF>.supabase.co`) is reachable only over IPv6, and Render connects over IPv4 only.
  The shared pooler works over IPv4.
- **Session mode (port 5432)** is what the API is set up for (see `backend/.env.example`).
  The transaction pooler (port 6543) also works, because the API turns off asyncpg's prepared-statement caching, but you don't need it.
- Paste the plain `postgresql://…` string. The API switches it to the asyncpg driver itself. Turn on TLS with `DATABASE_SSL=require` in step 2.2.

✅ **Check it worked:** the string contains `postgres.<PROJECT-REF>:`, `pooler.supabase.com:5432` and no `[YOUR-PASSWORD]`.

### 1.5 Create the database tables (17 migrations)

**Option A: Supabase CLI (recommended).** You don't need Docker for this.

```bash
npx supabase@latest login
npx supabase@latest link --project-ref <PROJECT-REF>
npx supabase@latest db push --remote --dry-run
npx supabase@latest db push --remote
```

1. `login` opens your browser. Approve, then return to the terminal. You should see "Finished supabase login".
2. `link` asks for the database password. Paste `<DB-PASSWORD>`. You should see "Finished supabase link".
3. `db push --remote --dry-run` lists the 17 files in `supabase/migrations/`, from `20260924000100_foundation.sql` to `20261114000200_source_documents_truncated.sql`, and changes nothing.
4. `db push --remote` shows the same list and asks to confirm. Type `y`. Every file prints "Applying migration …".

If the CLI says `unknown flag: --remote`, it's an older version. Use `--linked` instead of `--remote`.

> [!WARNING]
> Don't run `supabase config push`. `supabase/config.toml` is for local development. It would set the Site URL to
> `http://localhost:8443` and turn on Google sign-in without credentials.

**Option B: SQL Editor (fallback if the CLI doesn't work).** Open **SQL Editor** → **New query**. For each file below, in
this order, open the file in `supabase/migrations/`, copy all of it, paste it, and click **Run**. Wait for "Success" before you start the next one:

```text
20260924000100_foundation.sql
20260924000200_projects.sql
20260924000300_planning_domain.sql
20260924000400_project_list_index.sql
20261002002119_org_deletion_cleanup.sql
20261014061850_optimization_run_results.sql
20261015070000_planned_assignment_employee_cascade.sql
20261113000050_mockup_planning_inputs.sql
20261113000100_task_skill_write_policies.sql
20261113000200_apply_optimization_run.sql
20261113000300_intake_one_active_run.sql
20261113000400_ai_jobs.sql
20261113000500_source_documents_extracted_text.sql
20261113000520_documents_100mb.sql
20261113000600_apply_allocation_hours.sql
20261114000100_cross_project_links.sql
20261114000200_source_documents_truncated.sql
```

The SQL Editor doesn't record which migrations it ran. If you use Option B, don't run `db push` on this project
later, because it would try to apply everything again.

✅ **Check it worked:** **Table Editor** (schema `public`) lists 25 tables, including `organizations`, `projects`,
`wbs_nodes`, `optimization_runs` and `ai_jobs`. None of them is marked **Unrestricted** (every table has Row Level Security).

### 1.6 Sign-in settings for the demo

1. Open **Authentication** → **Sign In / Providers** and click **Email**.
2. Keep **Enable Email provider** on.
3. Turn **Confirm email** **off** and click **Save**.
   Why: without your own email server (SMTP), Supabase only sends emails to members of your Supabase team, and only
   2 per hour. With confirmation on, visitors couldn't finish signing up. With it off, sign-up signs them in directly.
4. Leave every other provider (Google, Azure, …) **off**. The login page shows a provider button only if the provider is on.
5. Optional: set **Minimum password length** to `10` and the requirement to lowercase, uppercase and digits.
   The sign-up form already asks for exactly this.

You set the Site URL and Redirect URLs in Part 4, once the website exists.

✅ **Check it worked:** the providers list shows **Email** as enabled and no other provider enabled.

---

## Part 2: The API (Render web service)

### 2.1 Connect Render to GitHub

1. In <https://dashboard.render.com> click **New** → **Web Service** → **Git Provider** → **GitHub**.
2. If the repository isn't listed, click **Configure account** (or **Connect GitHub**) and give the Render app
   access to `AI-Assisted-Project-Planning-Platform`.

✅ **Check it worked:** the repository appears in the list. Click it.

### 2.2 Create the web service

Fill in the form exactly like this:

| Field | Value |
|-------|-------|
| **Name** | `<API-NAME>` (e.g. `nirnain-api`) |
| **Language** | **Docker** |
| **Branch** | `<DEMO-BRANCH>` |
| **Region** | the one matching your Supabase region (table in step 1.1) |
| **Root Directory** | `backend` |
| **Dockerfile Path** | `./Dockerfile` (it's relative to the root directory, so this means `backend/Dockerfile`) |
| **Docker Build Context Directory** | `.` (the default; also relative to `backend`) |
| **Instance Type** | **Free** |

Under **Environment Variables**, click **+ Add Environment Variable** once per row. (Or click **Add from .env** and paste all the lines.)

| Key | Value | Why |
|-----|-------|-----|
| `ENVIRONMENT` | `production` | The API refuses to start without `DATABASE_URL` and `SUPABASE_URL`, and hides `/api/docs`. |
| `DATABASE_URL` | `<SESSION-POOLER-URL>` | From step 1.4. |
| `DATABASE_SSL` | `require` | Encrypts the database connection (the code's default is only `prefer`). |
| `SUPABASE_URL` | `<SUPABASE-URL>` | Exactly the same value as the website gets in step 3.1, with no trailing slash. It's used to check the token issuer and to find the signing keys. |
| `CORS_ORIGINS` | `https://<WEB-NAME>.onrender.com` | The website's origin, exactly: `https`, no trailing `/`. `*` is rejected. You'll confirm it in step 3.3. |
| `OPTIMIZER_WORKERS` | `1` | A free instance has less than one CPU (default is 4). |
| `DB_POOL_SIZE` | `3` | The free pooler allows 15 session clients. That leaves room while an old and a new instance overlap during a deploy. |
| `DB_MAX_OVERFLOW` | `2` | Same reason. |

Don't add these:

- **`PORT`**: Render sets it (10000), and the Dockerfile starts uvicorn on `${PORT}`.
- **`SUPABASE_JWT_SECRET`**: only if step 1.3 left you on the legacy secret.
- **`ANTHROPIC_API_KEY`**: optional, see step 2.4.

Open **Advanced** and set **Health Check Path** to `/health`. Leave **Auto-Deploy** on **On Commit**.

Click **Create Web Service**. The first build takes about 5–10 minutes.

✅ **Check it worked:**

- The **Logs** tab shows a line containing `Uvicorn running on http://0.0.0.0:10000`, and the service status turns **Live**.
- Copy the address at the top of the page (`https://….onrender.com`) as `<YOUR-API-URL>`.
- Open `<YOUR-API-URL>/health`. You should see `{"status":"ok","version":"dev"}`.
  ("dev" is expected: the API reads its version from a Railway-only variable.)
- Open `<YOUR-API-URL>/health/ready`. You should see `{"status":"ok","database":"ok"}`, which proves the database connection works.

### 2.3 Know the free-tier sleep (cold starts)

Render puts a free web service to sleep after **15 minutes without requests**. The next request wakes it, which takes **about a minute**.
While it's waking, the website's calls fail (the browser usually reports a CORS error or "Failed to fetch").
Only the API sleeps; the website (static site) is always on.

Before a demo, open `<YOUR-API-URL>/health` and wait until you see the JSON. Then use the website.
A sleeping service doesn't use free hours. A restart also stops an optimization or AI run that's in progress. The
run is marked failed when the API starts again, so start it again.

### 2.4 Optional: switch on the AI features (`ANTHROPIC_API_KEY`)

Without a key, the demo still works:

- AI intake uses the built-in rule-based extractor.
- WBS generation uses a template, and validation runs only the deterministic checks.
- The planning assistant answers "unavailable" (503).

To use Claude:

1. In the API service, open **Environment** → **+ Add Environment Variable**.
2. Key `ANTHROPIC_API_KEY`, value your key (`sk-ant-…`). Click **Save, rebuild, and deploy**.
3. Render keeps environment variables private to your workspace. Add the key **only to the API service**. Never put
   it in the repo, and never in the website's settings. Every `VITE_…` value is copied into the public website.
   (Render also passes environment variables to the Docker build. `backend/Dockerfile` doesn't use them, so the key isn't baked into the image.)

✅ **Check it worked:** after the redeploy, the logs no longer show `ANTHROPIC_API_KEY is not set`.

---

## Part 3: The website (Render static site)

Render builds the website from GitHub with your values, so you don't build anything on your computer.

### 3.1 Create the static site

1. In <https://dashboard.render.com> click **New** → **Static Site** and choose the repository.
2. Fill in the form exactly like this:

   | Field | Value |
   |-------|-------|
   | **Name** | `<WEB-NAME>` (e.g. `nirnain-web`) |
   | **Branch** | `<DEMO-BRANCH>` (the same branch as the API) |
   | **Root Directory** | leave empty (the website lives at the repository root) |
   | **Build Command** | `corepack pnpm@10.34.3 install --frozen-lockfile && corepack pnpm@10.34.3 build` |
   | **Publish Directory** | `dist` |

   About the build command: Corepack comes with Node.js and runs exactly the pnpm version the repo uses (10.34.3,
   pinned in `.mise.toml` and in CI). `package.json` has no `packageManager` field, so the version is written in
   the command.
3. Under **Environment Variables**, add:

   | Key | Value | Why |
   |-----|-------|-----|
   | `NODE_VERSION` | `22` | The Node.js version the repo uses (Render's default is newer). |
   | `SKIP_INSTALL_DEPS` | `true` | Stops Render's automatic install, so only the pnpm install in the build command runs. |
   | `VITE_SUPABASE_URL` | `<SUPABASE-URL>` | Same value as `SUPABASE_URL` on the API. |
   | `VITE_SUPABASE_PUBLISHABLE_KEY` | `<PUBLISHABLE-KEY>` | From step 1.2. |
   | `VITE_API_URL` | `<YOUR-API-URL>` | From step 2.2, no trailing `/`. |

   These values are copied into the website when it's built, so they are public. Only these three `VITE_…`
   values belong here. Never add a secret key or the database password.
4. Click **Create Static Site**. The build takes about 2 minutes.

✅ **Check it worked:**

- The deploy log shows `Done in … using pnpm v10.34.3` and `✓ built in …`, and the site status turns **Live**.
- Copy the address at the top of the page (`https://….onrender.com`) as `<YOUR-WEB-URL>`.
- Open `<YOUR-WEB-URL>`. You should see the NirnAIn landing page. A page listing missing settings (`VITE_…`) means
  a value is missing; see the troubleshooting table.

### 3.2 Add the rewrite rule (deep links and reloads)

The app has addresses like `/dashboard` and `/projects/<id>/scope`. Those are not files. Every one of them must be
answered with `index.html`, and then the app shows the right page.

1. In the static site, open **Redirects/Rewrites** and click **Add Rule**.
2. **Source** `/*`, **Destination** `/index.html`, **Action** **Rewrite**. Click **Save Changes**.

Render serves real files (such as `/assets/…`) as they are and applies the rule only to the other paths.

✅ **Check it worked:** open `<YOUR-WEB-URL>/dashboard` directly in a new tab. You see the sign-in page, because you
aren't signed in yet. Reload the page, and it's still the sign-in page, not "Not Found".

### 3.3 Make sure the API accepts the website

Compare `<YOUR-WEB-URL>` with the `CORS_ORIGINS` value you gave the API in step 2.2. They must be identical, for example
`https://nirnain-web.onrender.com`. If Render added a suffix to the name, update `CORS_ORIGINS`: open the API service,
go to **Environment**, edit the value, and click **Save, rebuild, and deploy**.

✅ **Check it worked:** the API's **Environment** page shows `CORS_ORIGINS` = `<YOUR-WEB-URL>` exactly.

### 3.4 Optional: rebuild the website only when it changes

With an empty root directory, every push to the branch rebuilds the website, even an API-only change. To skip those
builds, open the static site's **Settings** → **Build Filters** → **Included Paths** and add:

```text
src/**
index.html
package.json
pnpm-lock.yaml
vite.config.ts
tsconfig.json
.figma/make/site.json
```

---

## Part 4: Sign-in URLs (back in Supabase)

1. Open **Authentication** → **URL Configuration**.
2. **Site URL**: `<YOUR-WEB-URL>` (e.g. `https://nirnain-web.onrender.com`). Click **Save**.
3. **Redirect URLs**: click **Add URL**, enter `<YOUR-WEB-URL>/**` and save. (Keep `http://localhost:8443/**` too if
   you also run the app locally.)

This is where Supabase sends people back to, for example from a password-reset email. The app asks for
`<YOUR-WEB-URL>/auth/callback`, and the pattern above allows that.

✅ **Check it worked:** the page shows `<YOUR-WEB-URL>` as the Site URL and `<YOUR-WEB-URL>/**` in the list.

---

## Part 5: First login (end-to-end check)

1. Wake the API: open `<YOUR-API-URL>/health` and wait for `{"status":"ok",…}`.
2. Open `<YOUR-WEB-URL>` and click **Get Started**. Fill in first and last name, a real work email and a password (at
   least 10 characters, with upper- and lowercase letters and a digit), then click **Create account**.
3. Because **Confirm email** is off, you land on the dashboard straight away. The database creates a profile and a
   workspace named "<your name>'s Workspace" for every new user.

✅ **Check it worked:**

- The dashboard loads without an error message. It shows your workspace and an empty project list.
- Reload the page at `<YOUR-WEB-URL>/dashboard`. You stay on the dashboard, still signed in.
- Supabase **Authentication** → **Users** lists your email.
- The API's **Logs** show lines like `"path": "/api/v1/me", "status": 200`.
- Optional: in the browser's developer tools (F12) → **Network**, the calls to `<YOUR-API-URL>/api/v1/…` return 200.

Then click **+ Create Project** to try the whole flow.

---

## Part 6 (optional): Load demo data

The demo seed script fills the hosted demo with sample users, projects and plans. It talks to the same public
addresses as the website, so it needs `<SUPABASE-URL>`, `<PUBLISHABLE-KEY>` and `<YOUR-API-URL>`. **Follow the
seed script's README** for the exact command. Before you run it:

- Wake the API (step 2.3).
- Keep **Confirm email** off (step 1.6) if the script creates users.

✅ **Check it worked:** sign in with a demo account from the seed README. Its projects appear on the dashboard.

---

## Updating the demo later

| What changed | What to do |
|--------------|------------|
| Code on `<DEMO-BRANCH>` | Nothing. Render redeploys automatically. The API rebuilds only for changes under `backend/`. The website rebuilds for every push, or only for website files if you set step 3.4. |
| A website setting (`VITE_…`) | Static site → **Environment** → edit → **Save, rebuild, and deploy**. The website needs a rebuild to pick up new values. |
| An API setting | API service → **Environment** → edit → **Save, rebuild, and deploy**. |
| Database (new file in `supabase/migrations/`) | `npx supabase@latest db push --remote` (step 1.5, Option A). |
| Supabase project paused (no activity for a week) | Supabase dashboard → the project → **Restore project**. Data is kept. |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| **CORS error** in the browser console ("blocked by CORS policy: No 'Access-Control-Allow-Origin' header") | `CORS_ORIGINS` on the API doesn't exactly match the website origin. Or the API is asleep or crashed: Render's wake-up and error pages have no CORS headers. | Open `<YOUR-API-URL>/health` first. If that works, set `CORS_ORIGINS` to exactly `<YOUR-WEB-URL>` (`https://<WEB-NAME>.onrender.com`, no trailing `/`). Save, then wait for the redeploy. |
| **401** on `/api/v1/…` calls (the app signs you out or says you're not signed in) | `SUPABASE_URL` on the API differs from `VITE_SUPABASE_URL` on the website (the token issuer doesn't match), or the project still signs tokens with the legacy secret and `SUPABASE_JWT_SECRET` isn't set. A session left over from another project also causes this. | Make both URLs identical: `https://<PROJECT-REF>.supabase.co`, no trailing `/`. Check `jwks.json` (step 1.3): rotate to ECC, or set `SUPABASE_JWT_SECRET`. Sign out and sign in again. |
| **503** "Authentication provider is unavailable" | The API couldn't download `…/auth/v1/.well-known/jwks.json`. | Check that `SUPABASE_URL` is right and the Supabase project isn't paused. |
| API deploy fails, logs say "Missing required settings in production" or show a settings error | `DATABASE_URL` or `SUPABASE_URL` is missing, or a value is invalid (e.g. `CORS_ORIGINS=*`). | Fix the variable under **Environment**, then click **Save, rebuild, and deploy**. |
| `/health/ready` says **"Database is unreachable"** | Wrong password or host, the direct `db.<PROJECT-REF>.supabase.co` host was used (IPv6 only, unreachable from Render), the password has unencoded symbols, or the project is paused. | Use the **Session pooler** URI (step 1.4). If needed, reset the password (Project Settings → Database) to letters and digits only, and update `DATABASE_URL`. Restore a paused project. |
| API logs show **`MaxClientsInSessionMode: max clients reached`** | Too many pooled connections, e.g. during a deploy. | Keep `DB_POOL_SIZE=3` and `DB_MAX_OVERFLOW=2`. As a last resort, use the Transaction pooler URI (port 6543). |
| **First request takes about a minute**, or the site says the service is unavailable after a break | The free API service was asleep (cold start). | Open `<YOUR-API-URL>/health`, wait for the JSON, then reload the website. |
| **"Not Found"** when you open or reload `<YOUR-WEB-URL>/dashboard` (or any page other than the home page) | The rewrite rule is missing or wrong. | Add the rule from step 3.2: Source `/*`, Destination `/index.html`, Action **Rewrite**. |
| Website shows a page listing missing settings (`VITE_…`), or a blank page | A `VITE_…` value was missing or misspelled when the site was built. | Fix it under the static site's **Environment**, then click **Save, rebuild, and deploy**. Values only take effect after a rebuild. |
| Website build fails: `corepack: command not found` | The site runs on a Node.js version without Corepack. | Set `NODE_VERSION` = `22` on the static site and deploy again. |
| Website build fails: `ERR_PNPM_OUTDATED_LOCKFILE` or `npm ERR!` | `pnpm-lock.yaml` doesn't match `package.json` on that branch, or `SKIP_INSTALL_DEPS` is missing, so Render ran its own install. | Check `SKIP_INSTALL_DEPS` = `true`. If the lockfile is the problem, ask the frontend team to update it. |
| Old version still showing after a deploy | Browser cache | Hard reload: Ctrl+Shift+R (Cmd+Shift+R on a Mac). |
| **Migrations fail**: "password authentication failed" | Wrong database password | Run `link` again with the right `<DB-PASSWORD>`. |
| **Migrations fail**: "already exists" | Some files were already run (e.g. part of Option B), or this isn't a fresh project. | For a demo, the quickest fix is a fresh project (step 1.1). Otherwise ask the database team about `supabase migration repair`. |
| **Migrations fail**: timeout or "could not connect" | Network or firewall | Retry, or add `--debug` to see details. Use Option B (SQL Editor) as a fallback. |
| Sign-up says **"Email address not authorized"**, or no confirmation email arrives | **Confirm email** is still on. Supabase's built-in mailer only sends to your team, 2 per hour. | Turn **Confirm email** off (step 1.6). |
| Password-reset email never arrives | Same mailer limits | Test with an address that's a member of your Supabase team, or set up custom SMTP (Authentication → Emails → SMTP). |

---

## Optional code changes

The guide works with the code as it is. These would only make it tidier:

- **`/health` version:** `backend/app/core/config.py` could also read `RENDER_GIT_COMMIT`, which Render sets, so `/health` shows the deployed commit instead of `dev`.
- **pnpm version in one place:** adding `"packageManager": "pnpm@10.34.3"` to `package.json` would let the build command drop the version (`corepack pnpm install --frozen-lockfile && corepack pnpm build`).
  Change CI's `pnpm/action-setup` `version:` at the same time, so the version isn't specified twice.

---

## Appendix A: Hugging Face static Space (not recommended)

Hugging Face static Spaces are free, but they can't serve the app's deep links:

- A static Space returns only files that exist. Opening or reloading `/dashboard` answers "Entry not found".
- The site starts at `/index.html`, which the app treats as an unknown page ("We can't find that page").
- There is no rewrite rule, and a `404.html` file isn't used as a fallback. We tested this on public Spaces.

Hosting there would need hash routing: addresses like `/index.html#/dashboard`, switched on at build time with
`VITE_ROUTER=hash`. That code change was prototyped as "W1" but isn't planned. It's kept as a reference only.
Use the Render static site from Part 3 instead.

---

## Sources

Checked in November 2026.

- Render free instances (spin-down after 15 min, about 1 minute to wake, 750 hours/month, free static sites): <https://render.com/docs/free>
- Render static sites (`SKIP_INSTALL_DEPS`): <https://render.com/docs/static-sites>
- Render redirects and rewrites (rules don't override existing files): <https://render.com/docs/redirects-rewrites>
- Render Node.js version (`NODE_VERSION`): <https://render.com/docs/node-version>
- Render Docker services (Dockerfile path, environment variables passed as build args): <https://render.com/docs/docker>
- Render web services (`PORT`, default 10000): <https://render.com/docs/web-services#port-binding>
- Render root directory and build filters: <https://render.com/docs/monorepo-support>
- Render health checks: <https://render.com/docs/health-checks>
- Render regions: <https://render.com/docs/regions>
- Render environment variables: <https://render.com/docs/configure-environment-variables>
- Render pricing (free instance: 512 MB RAM, under 1 CPU): <https://render.com/pricing>
- Corepack (`corepack <name>@<version> …`): <https://github.com/nodejs/corepack#readme>
- Supabase connection strings, IPv4/IPv6, poolers, asyncpg prepared statements: <https://supabase.com/docs/guides/database/connecting-to-postgres>
- Render is IPv4-only (listed by Supabase): <https://supabase.com/docs/guides/platform/ipv4-address>
- Supabase pooling limits: <https://supabase.com/docs/guides/database/connecting-to-postgres/pooling-and-limits>, <https://supabase.com/docs/guides/platform/compute-and-disk>
- Supabase API keys (publishable vs secret): <https://supabase.com/docs/guides/getting-started/api-keys>
- Supabase JWT signing keys and JWKS endpoint: <https://supabase.com/docs/guides/auth/signing-keys>
- Supabase redirect URLs and Site URL: <https://supabase.com/docs/guides/auth/redirect-urls>
- Supabase email confirmation default: <https://supabase.com/docs/guides/auth/passwords>
- Supabase built-in email limits: <https://supabase.com/docs/guides/auth/auth-smtp>
- Supabase CLI (`link`, `db push --remote`): <https://supabase.com/docs/reference/cli/supabase-db-push>, <https://supabase.com/docs/reference/cli/supabase-link>
- Supabase pricing (free projects pause after 1 week of inactivity): <https://supabase.com/pricing>
- Hugging Face static Spaces (Appendix A): <https://huggingface.co/docs/hub/spaces-sdks-static>, <https://huggingface.co/docs/hub/spaces-config-reference>
