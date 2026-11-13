-- =====================================================================
-- AI jobs: background WBS generation and validation.
--
--   kind    : wbs_generate | wbs_validate
--   result  : wbs_generate -> { deliverables, workPackages, dependencies } counts
--             wbs_validate -> { structureValid, levels, issues: [{ type, text }] }
--
-- The API inserts a queued row under the caller's RLS session; the backend
-- worker claims it and writes the outcome on its service connection, so
-- users get no update/delete policies.
-- =====================================================================

create type public.ai_job_kind as enum ('wbs_generate', 'wbs_validate');

create table public.ai_jobs (
  id             uuid primary key default gen_random_uuid(),
  project_id     uuid not null references public.projects (id) on delete cascade,
  kind           public.ai_job_kind not null,
  status         public.job_status not null default 'queued',
  model          text,
  input_tokens   integer check (input_tokens >= 0),
  output_tokens  integer check (output_tokens >= 0),
  result         jsonb,
  error          text,
  requested_by   uuid references auth.users (id) on delete set null default auth.uid(),
  created_at     timestamptz not null default now(),
  started_at     timestamptz,
  finished_at    timestamptz
);

comment on table public.ai_jobs is
  'Background AI jobs (WBS generate/validate). Written by the backend worker; users only queue and read.';

create index ai_jobs_project_kind_idx on public.ai_jobs (project_id, kind, created_at desc);
create index ai_jobs_requested_by_idx on public.ai_jobs (requested_by);

-- At most one queued/running job of each kind per project.
create unique index ai_jobs_one_active_per_project_kind
  on public.ai_jobs (project_id, kind)
  where status in ('queued', 'running');

alter table public.ai_jobs enable row level security;

create policy "ai_jobs: project readers can read"
  on public.ai_jobs for select to authenticated
  using ((select private.can_read_project(project_id)));

-- Users can only queue a job for themselves; the worker sets everything else.
create policy "ai_jobs: project writers can insert"
  on public.ai_jobs for insert to authenticated
  with check (
    (select private.can_write_project(project_id))
    and status = 'queued'
    and requested_by = (select auth.uid())
  );

revoke all on public.ai_jobs from anon;
