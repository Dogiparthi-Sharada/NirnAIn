# Host the NirnAIn demo for free

This guide takes you from zero to a working public demo, one step at a time:

| Part | Runs on | Free tier | What it is |
|------|---------|-----------|------------|
| Database + sign-in | **Supabase** (new free project) | 2 free projects; paused after 1 week without activity | Postgres, Auth |
| API | **Render** free web service (Docker) | 750 instance hours/month; sleeps after 15 min idle | `backend/` (FastAPI) |
| Website | **Hugging Face** static Space | Static Spaces are free for everyone | `src/` (React, built to `dist/`) |

Railway and the old Supabase project are no longer used. Nothing in this guide touches them.

Plan for 60–90 minutes. Do the parts in this order: Supabase, Render, Hugging Face, back to Supabase for the sign-in URLs, then the first login.

> [!IMPORTANT]
> **Part 3 (Hugging Face) needs code change W1.** A static Space serves files exactly as uploaded. It
> can't send `index.html` for addresses like `/dashboard`, and it opens the site at `/index.html`. The
> app currently uses normal URLs (`/dashboard`), so on a static Space it shows "We can't find that page".
> Code change W1 adds hash URLs (`/index.html#/dashboard`), which work on a static Space.
> Check whether you have it: open `.env.example` in the repo. If it mentions `VITE_ROUTER`, W1 is in.
> If not, ask the frontend team to merge W1 first (see [Required code changes](#required-code-changes)),
> or host the website on a Render Static Site instead ([Appendix A](#appendix-a-website-on-a-render-static-site-no-code-change)). That option needs no code change.

---

## Before you start

### Accounts (all free)

- GitHub access to this repository (Render deploys the API straight from GitHub).
- Supabase: <https://supabase.com/dashboard/sign-up>
- Render: <https://dashboard.render.com/register> (sign up with GitHub, which makes step 2.1 easier).
- Hugging Face: <https://huggingface.co/join>

### Tools on your computer

- **Git**: <https://git-scm.com/downloads>
- **Node.js 22 LTS**: <https://nodejs.org/>. It includes `npm` and `npx`.
- **pnpm 10**: after installing Node, run `npm install -g pnpm@10.34.3` in a terminal.

Check it worked: `node --version` prints `v22.…` and `pnpm --version` prints `10.34.3`.

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
| `<YOUR-RENDER-URL>` | `https://nirnain-api.onrender.com` | 2.2 |
| `<HF-USER>`, `<YOUR-SPACE>` | `jane`, `nirnain-demo` | 3.1 |
| `<YOUR-SPACE-URL>` | `https://jane-nirnain-demo.static.hf.space` | 3.2 |

Decide `<HF-USER>` and `<YOUR-SPACE>` now, using lowercase letters, digits and `-` only. The Space address is
predictable: `https://<HF-USER>-<YOUR-SPACE>.static.hf.space`. You need it for Render in step 2.2, before the Space exists.

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

✅ **Check it worked:** **Current key** shows ECC (P-256) or RSA (or you set `SUPABASE_JWT_SECRET` aside for step 2.2).
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

### 1.5 Create the database tables (15 migrations)

**Option A: Supabase CLI (recommended).** You don't need Docker for this.

```bash
npx supabase@latest login
npx supabase@latest link --project-ref <PROJECT-REF>
npx supabase@latest db push --remote --dry-run
npx supabase@latest db push --remote
```

1. `login` opens your browser. Approve, then return to the terminal. You should see "Finished supabase login".
2. `link` asks for the database password. Paste `<DB-PASSWORD>`. You should see "Finished supabase link".
3. `db push --remote --dry-run` lists the 15 files in `supabase/migrations/`, from `20260924000100_foundation.sql` to `20261113000600_apply_allocation_hours.sql`, and changes nothing.
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

## Part 2: Render (the API)

### 2.1 Connect Render to GitHub

1. In <https://dashboard.render.com> click **New** → **Web Service** → **Git Provider** → **GitHub**.
2. If the repository isn't listed, click **Configure account** (or **Connect GitHub**) and give the Render app
   access to `AI-Assisted-Project-Planning-Platform`.

✅ **Check it worked:** the repository appears in the list. Click it.

### 2.2 Create the web service

Fill in the form exactly like this:

| Field | Value |
|-------|-------|
| **Name** | `nirnain-api` (this becomes `https://nirnain-api.onrender.com`; Render adds a suffix if the name is taken) |
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
| `SUPABASE_URL` | `<SUPABASE-URL>` | Exactly the same value as the website gets in step 3.3, with no trailing slash. It's used to check the token issuer and to find the signing keys. |
| `CORS_ORIGINS` | `https://<HF-USER>-<YOUR-SPACE>.static.hf.space` | The website's origin, exactly: `https`, no trailing `/`, no `/index.html`. `*` is rejected. You'll confirm it in step 3.2. |
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
- Copy the address at the top of the page (`https://….onrender.com`) as `<YOUR-RENDER-URL>`.
- Open `<YOUR-RENDER-URL>/health`. You should see `{"status":"ok","version":"dev"}`.
  ("dev" is expected: the API reads its version from a Railway-only variable.)
- Open `<YOUR-RENDER-URL>/health/ready`. You should see `{"status":"ok","database":"ok"}`, which proves the database connection works.

### 2.3 Know the free-tier sleep (cold starts)

Render puts a free web service to sleep after **15 minutes without requests**. The next request wakes it, which takes **about a minute**.
While it's waking, the website's calls fail (the browser usually reports a CORS error or "Failed to fetch").

Before a demo, open `<YOUR-RENDER-URL>/health` and wait until you see the JSON. Then use the website.
A sleeping service doesn't use free hours. A restart also stops an optimization or AI run that's in progress. The
run is marked failed when the API starts again, so start it again.

### 2.4 Optional: switch on the AI features (`ANTHROPIC_API_KEY`)

Without a key, the demo still works:

- AI intake uses the built-in rule-based extractor.
- WBS generation uses a template, and validation runs only the deterministic checks.
- The planning assistant answers "unavailable" (503).

To use Claude:

1. In your Render service, open **Environment** → **+ Add Environment Variable**.
2. Key `ANTHROPIC_API_KEY`, value your key (`sk-ant-…`). Click **Save, rebuild, and deploy**.
3. Render keeps environment variables private to your workspace. Never put the key in the repo, in a `VITE_…`
   variable, in `.env.production.local` or in the Hugging Face Space. Anything there becomes public.
   (Render also passes environment variables to the Docker build. `backend/Dockerfile` doesn't use them, so the key isn't baked into the image.)

✅ **Check it worked:** after the redeploy, the logs no longer show `ANTHROPIC_API_KEY is not set`.

---

## Part 3: Hugging Face (the website, needs code change W1)

### 3.1 Create the Space

1. Open <https://huggingface.co/new-space>.
2. **Owner**: you (`<HF-USER>`). **Space name**: `<YOUR-SPACE>` (e.g. `nirnain-demo`).
3. **SDK**: **Static**. **Template**: **Blank**.
4. **Visibility**: **Public**. **Private** would make the website unreachable for visitors, and **Protected** needs a paid plan.
   Public means anyone can browse the uploaded files. That's fine: they are the same files any visitor's browser downloads.
5. Click **Create Space**.

✅ **Check it worked:** the Space page shows the blank template. Its **Files** tab contains `README.md` and `index.html`.
`README.md` starts with a block that includes `sdk: static`. Keep that file; it's how Hugging Face knows how to serve the Space.

### 3.2 Confirm the Space address (`<YOUR-SPACE-URL>`)

Open `https://huggingface.co/api/spaces/<HF-USER>/<YOUR-SPACE>` in your browser and find `"host":`. For example
`"host":"https://jane-nirnain-demo.static.hf.space"`. That value is `<YOUR-SPACE-URL>` and also the website's origin.

- Static Spaces are served from **`*.static.hf.space`**. The `https://<HF-USER>-<YOUR-SPACE>.hf.space` form (without `static`) doesn't serve them.
- If the host differs from the `CORS_ORIGINS` you set in step 2.2, fix it on Render now: **Environment** → edit → **Save, rebuild, and deploy**.

✅ **Check it worked:** opening `<YOUR-SPACE-URL>` redirects to `<YOUR-SPACE-URL>/index.html` and shows the blank template page.

### 3.3 Build the website with the hosted values

The website reads its settings when it's **built**: Vite copies every `VITE_…` value into the JavaScript files.
So build on your computer with the hosted values, then upload the result.

1. In the repo folder, create a file named `.env.production.local`. It's git-ignored, and `pnpm build` reads it automatically. Put this in it:

   ```dotenv
   VITE_SUPABASE_URL=https://<PROJECT-REF>.supabase.co
   VITE_SUPABASE_PUBLISHABLE_KEY=<PUBLISHABLE-KEY>
   VITE_API_URL=<YOUR-RENDER-URL>
   # Needs code change W1: hash URLs (/index.html#/dashboard) for the static Space
   VITE_ROUTER=hash
   ```

   Use no quotes and no trailing `/`. Only public values belong here, because everything here ends up in the public website.

2. Build:

   ```bash
   pnpm install --frozen-lockfile
   pnpm build
   ```

✅ **Check it worked:**

- The build ends with `✓ built in …`.
- `dist/` contains `index.html`, `robots.txt` and an `assets/` folder (about 1 MB in total).
- Search `dist/assets` for your Render host name (e.g. `onrender.com`): one `.js` file contains it. If it's missing, the
  values weren't read. Check the file name `.env.production.local` and run `pnpm build` again.

Why build locally? A static Space can run a build itself (`app_build_command`), but Space variables are only
available to the page while it runs (`window.huggingface.variables`). They aren't `VITE_…` build values, and the repo
uses pnpm. Uploading `dist/` is simpler and predictable.

### 3.4 Upload `dist/` to the Space

**Option A: the `hf` command-line tool (recommended; it uploads the `assets/` folder reliably).**

1. Install it.
   - macOS/Linux: `curl -LsSf https://hf.co/cli/install.sh | bash`
   - Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://hf.co/cli/install.ps1 | iex"`
2. Create a token with **Write** access at <https://huggingface.co/settings/tokens>, then run `hf auth login` and paste it.
3. Upload. This puts the contents of `dist/` at the root of the Space and removes old `assets/` files on later uploads:

   ```bash
   hf upload <HF-USER>/<YOUR-SPACE> ./dist . --repo-type=space --delete="assets/*" --commit-message="Deploy NirnAIn website"
   ```

**Option B: in the browser.** In the Space, open **Files** → **+ Contribute** → **Upload files**. Drag in `index.html`,
`robots.txt` **and the whole `assets` folder** from `dist/`, then click **Commit changes to main**.

Both ways, `README.md` stays (don't upload the repo's own README). You can delete the template's `style.css`; it's unused.

✅ **Check it worked:**

- The **Files** tab shows `index.html`, `robots.txt` and `assets/…`.
- `<YOUR-SPACE-URL>` shows the NirnAIn landing page.
- `<YOUR-SPACE-URL>/index.html#/login` shows the sign-in page, and it still shows it after you reload the page.

If you see "We can't find that page" at `/index.html`, the build doesn't have hash URLs (W1 missing, or
`VITE_ROUTER=hash` not set).

Share **`<YOUR-SPACE-URL>`** (the direct address), not the `huggingface.co/spaces/…` page. That page shows the site
inside a frame, where some browsers (e.g. Safari) don't keep visitors signed in.

---

## Part 4: Sign-in URLs (back in Supabase)

1. Open **Authentication** → **URL Configuration**.
2. **Site URL**: `<YOUR-SPACE-URL>` (e.g. `https://jane-nirnain-demo.static.hf.space`). Click **Save**.
3. **Redirect URLs**: click **Add URL**, enter `<YOUR-SPACE-URL>/**` and save. (Keep `http://localhost:8443/**` too if
   you also run the app locally.)

This is where Supabase may send people back to, for example from a password-reset email. With W1, the app asks for
`<YOUR-SPACE-URL>/index.html?…#/auth/callback`, and the pattern above allows that.

✅ **Check it worked:** the page shows your Space URL as the Site URL and `<YOUR-SPACE-URL>/**` in the list.

---

## Part 5: First login (end-to-end check)

1. Wake the API: open `<YOUR-RENDER-URL>/health` and wait for `{"status":"ok",…}`.
2. Open `<YOUR-SPACE-URL>` and click **Get Started**. Fill in first and last name, a real work email and a password (at
   least 10 characters, with upper- and lowercase letters and a digit), then click **Create account**.
3. Because **Confirm email** is off, you land on the dashboard straight away. The database creates a profile and a
   workspace named "<your name>'s Workspace" for every new user.

✅ **Check it worked:**

- The dashboard loads without an error message. It shows your workspace and an empty project list.
- Supabase **Authentication** → **Users** lists your email.
- Render **Logs** show lines like `"path": "/api/v1/me", "status": 200`.
- Optional: in the browser's developer tools (F12) → **Network**, the calls to `<YOUR-RENDER-URL>/api/v1/…` return 200.

Then click **+ Create Project** to try the whole flow.

---

## Part 6 (optional): Load demo data

The demo seed script fills the hosted demo with sample users, projects and plans. It talks to the same public
addresses as the website, so it needs `<SUPABASE-URL>`, `<PUBLISHABLE-KEY>` and `<YOUR-RENDER-URL>`. **Follow the
seed script's README** for the exact command. Before you run it:

- Wake the API (step 2.3).
- Keep **Confirm email** off (step 1.6) if the script creates users.

✅ **Check it worked:** sign in with a demo account from the seed README. Its projects appear on the dashboard.

---

## Updating the demo later

| What changed | What to do |
|--------------|------------|
| API (`backend/`) | Push or merge to `<DEMO-BRANCH>`. Render redeploys automatically. With Root Directory `backend`, only changes under `backend/` trigger a deploy. |
| Website (`src/` …) | Pull the latest code, run `pnpm build`, run the `hf upload …` command from step 3.4 again. |
| Database (new file in `supabase/migrations/`) | `npx supabase@latest db push --remote` (step 1.5, Option A). |
| Supabase project paused (no activity for a week) | Supabase dashboard → the project → **Restore project**. Data is kept. |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| **CORS error** in the browser console ("blocked by CORS policy: No 'Access-Control-Allow-Origin' header") | `CORS_ORIGINS` on Render doesn't exactly match the website origin. Or the API is asleep or crashed: Render's wake-up and error pages have no CORS headers. | Open `<YOUR-RENDER-URL>/health` first. If that works, set `CORS_ORIGINS` to the exact `"host"` from step 3.2 (`https://…static.hf.space`, no trailing `/`, no `/index.html`). Save, then wait for the redeploy. |
| **401** on `/api/v1/…` calls (the app signs you out or says you're not signed in) | `SUPABASE_URL` on Render differs from `VITE_SUPABASE_URL` (the token issuer doesn't match), or the project still signs tokens with the legacy secret and `SUPABASE_JWT_SECRET` isn't set. A session left over from another project also causes this. | Make both URLs identical: `https://<PROJECT-REF>.supabase.co`, no trailing `/`. Check `jwks.json` (step 1.3): rotate to ECC, or set `SUPABASE_JWT_SECRET`. Sign out and sign in again. |
| **503** "Authentication provider is unavailable" | The API couldn't download `…/auth/v1/.well-known/jwks.json`. | Check that `SUPABASE_URL` is right and the Supabase project isn't paused. |
| Render deploy fails, logs say "Missing required settings in production" or show a settings error | `DATABASE_URL` or `SUPABASE_URL` is missing, or a value is invalid (e.g. `CORS_ORIGINS=*`). | Fix the variable under **Environment**, then click **Save, rebuild, and deploy**. |
| `/health/ready` says **"Database is unreachable"** | Wrong password or host, the direct `db.<PROJECT-REF>.supabase.co` host was used (IPv6 only, unreachable from Render), the password has unencoded symbols, or the project is paused. | Use the **Session pooler** URI (step 1.4). If needed, reset the password (Project Settings → Database) to letters and digits only, and update `DATABASE_URL`. Restore a paused project. |
| Logs show **`MaxClientsInSessionMode: max clients reached`** | Too many pooled connections, e.g. during a deploy. | Keep `DB_POOL_SIZE=3` and `DB_MAX_OVERFLOW=2`. As a last resort, use the Transaction pooler URI (port 6543). |
| **First request takes about a minute**, or the site says the service is unavailable after a break | The free Render service was asleep (cold start). | Open `<YOUR-RENDER-URL>/health`, wait for the JSON, then reload the website. |
| **"Entry not found"** after reloading a page, or **"We can't find that page"** at `<YOUR-SPACE-URL>/index.html` | The website was built without hash URLs (W1 missing, or `VITE_ROUTER=hash` not set), or a link without `#` was used (e.g. `/dashboard`). | Rebuild with W1 and `VITE_ROUTER=hash` (step 3.3), then upload again. Links look like `<YOUR-SPACE-URL>/index.html#/dashboard`. |
| **Blank page** or a page listing missing settings (`VITE_…`) | The `VITE_…` values weren't available during `pnpm build`. | Check `.env.production.local` (name, location, no quotes). Run `pnpm build` and upload again. |
| Old version still showing after an upload | Browser cache | Hard reload: Ctrl+Shift+R (Cmd+Shift+R on a Mac). |
| **Migrations fail**: "password authentication failed" | Wrong database password | Run `link` again with the right `<DB-PASSWORD>`. |
| **Migrations fail**: "already exists" | Some files were already run (e.g. part of Option B), or this isn't a fresh project. | For a demo, the quickest fix is a fresh project (step 1.1). Otherwise ask the database team about `supabase migration repair`. |
| **Migrations fail**: timeout or "could not connect" | Network or firewall | Retry, or add `--debug` to see details. Use Option B (SQL Editor) as a fallback. |
| Sign-up says **"Email address not authorized"**, or no confirmation email arrives | **Confirm email** is still on. Supabase's built-in mailer only sends to your team, 2 per hour. | Turn **Confirm email** off (step 1.6). |
| Password-reset email never arrives | Same mailer limits | Test with an address that's a member of your Supabase team, or set up custom SMTP (Authentication → Emails → SMTP). |

---

## Required code changes

| Id | Needed for | Status | Change |
|----|------------|--------|--------|
| **W1** | Part 3 (Hugging Face static Space) | **Required**, not in the code yet | Hash URLs when built with `VITE_ROUTER=hash` (details below). Without it, use Appendix A. |
| N1 | `/health` version | Nice to have | `backend/app/core/config.py`: also read `RENDER_GIT_COMMIT` (set by Render) so `/health` shows the commit instead of `dev`. |

**W1: hash routing for static hosting.** A static Space has no "send `index.html` for any path" option. We verified that a
`404.html` isn't used, folders don't serve their `index.html`, and `/` redirects to `/index.html`.

- `src/App.tsx`: use `createHashRouter` instead of `createBrowserRouter` when `import.meta.env.VITE_ROUTER === 'hash'`.
- `src/lib/redirect.ts`: add `USE_HASH_ROUTER` and `appHref(path)` (`#${path}` in hash mode). In hash mode,
  `authRedirectUrl()` returns `${origin}${pathname}${?next=…}#/auth/callback`.
  Supabase adds `?code=` to the real query string and keeps the `#/auth/callback` part, so `AuthCallback` keeps reading `next` and `code` from `window.location.search`.
- `src/components/ErrorPages.tsx`: the plain `<a href="/dashboard">` becomes `<a href={appHref('/dashboard')}>`.
- `src/vite-env.d.ts`: add `readonly VITE_ROUTER?: 'browser' | 'hash'`.
- `.env.example`: document `VITE_ROUTER` (default `browser`; `hash` for static hosts without an `index.html` fallback).

Railway-style hosting keeps normal URLs, because `browser` stays the default.

---

## Appendix A: Website on a Render Static Site (no code change)

Use this if W1 isn't merged yet. Render Static Sites are free and can serve `index.html` for every path, so the app's
normal URLs (`/dashboard`) work as they are. Do it in place of Part 3. The website origin is then
`https://<site-name>.onrender.com`; use it for `CORS_ORIGINS` (step 2.2) and in Part 4.

1. Render → **New** → **Static Site** → choose the repository.
2. **Name**: `nirnain-web`. **Branch**: `<DEMO-BRANCH>`. **Root Directory**: leave empty.
3. **Build Command**: `npx --yes pnpm@10.34.3 install --frozen-lockfile && npx --yes pnpm@10.34.3 build`
4. **Publish Directory**: `dist`
5. **Environment Variables**:
   - `NODE_VERSION` = `22`
   - `SKIP_INSTALL_DEPS` = `true`
   - `VITE_SUPABASE_URL` = `<SUPABASE-URL>`
   - `VITE_SUPABASE_PUBLISHABLE_KEY` = `<PUBLISHABLE-KEY>`
   - `VITE_API_URL` = `<YOUR-RENDER-URL>`

   Don't set `VITE_ROUTER`.
6. Click **Create Static Site**. Then open **Redirects/Rewrites** and add a rule: **Source** `/*`, **Destination** `/index.html`, **Action** **Rewrite**.
   Render serves real files (`/assets/…`) first and applies the rule only to the other paths.
7. On the API service, set `CORS_ORIGINS` to `https://nirnain-web.onrender.com` (your actual site address).
   In Part 4, use that address as the Site URL and `https://nirnain-web.onrender.com/**` as the Redirect URL.

✅ **Check it worked:** `https://nirnain-web.onrender.com/dashboard` loads the app (or the sign-in page), and still does after a reload.

---

## Sources

Checked in November 2026.

- Hugging Face static Spaces: <https://huggingface.co/docs/hub/spaces-sdks-static>
- Hugging Face Spaces configuration (`sdk`, `app_file`, `app_build_command`): <https://huggingface.co/docs/hub/spaces-config-reference>
- Hugging Face Spaces overview (static Spaces free; Gradio/Docker need a paid plan; visibility): <https://huggingface.co/docs/hub/spaces-overview>
- Hugging Face CLI (`hf auth login`, `hf upload`): <https://huggingface.co/docs/huggingface_hub/guides/cli>
- Hugging Face storage limits: <https://huggingface.co/docs/hub/storage-limits>
- We also tested public static Spaces: `/` redirects to `/index.html`, unknown paths return "Entry not found" even when a
  `404.html` exists, and the `"host"` returned by `https://huggingface.co/api/spaces/<owner>/<name>` is `https://<owner>-<name>.static.hf.space`.
- Render free instances (spin-down after 15 min, about 1 minute to wake, 750 hours/month): <https://render.com/docs/free>
- Render Docker services (Dockerfile path, environment variables passed as build args): <https://render.com/docs/docker>
- Render web services (`PORT`, default 10000): <https://render.com/docs/web-services#port-binding>
- Render root directory (monorepos): <https://render.com/docs/monorepo-support>
- Render health checks: <https://render.com/docs/health-checks>
- Render regions: <https://render.com/docs/regions>
- Render environment variables: <https://render.com/docs/configure-environment-variables>
- Render is IPv4-only (listed by Supabase): <https://supabase.com/docs/guides/platform/ipv4-address>
- Render static sites, redirects and rewrites: <https://render.com/docs/static-sites>, <https://render.com/docs/redirects-rewrites>
- Render pricing (free instance: 512 MB RAM, under 1 CPU): <https://render.com/pricing>
- Supabase connection strings, IPv4/IPv6, poolers, asyncpg prepared statements: <https://supabase.com/docs/guides/database/connecting-to-postgres>
- Supabase pooling limits: <https://supabase.com/docs/guides/database/connecting-to-postgres/pooling-and-limits>, <https://supabase.com/docs/guides/platform/compute-and-disk>
- Supabase API keys (publishable vs secret): <https://supabase.com/docs/guides/getting-started/api-keys>
- Supabase JWT signing keys and JWKS endpoint: <https://supabase.com/docs/guides/auth/signing-keys>
- Supabase redirect URLs and Site URL: <https://supabase.com/docs/guides/auth/redirect-urls>
- Supabase email confirmation default: <https://supabase.com/docs/guides/auth/passwords>
- Supabase built-in email limits: <https://supabase.com/docs/guides/auth/auth-smtp>
- Supabase CLI (`link`, `db push --remote`): <https://supabase.com/docs/reference/cli/supabase-db-push>, <https://supabase.com/docs/reference/cli/supabase-link>
- Supabase pricing (free projects pause after 1 week of inactivity): <https://supabase.com/pricing>
