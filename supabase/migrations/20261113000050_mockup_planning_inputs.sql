-- =====================================================================
-- Planning inputs for the approved NirnAIn workflow (mockup handoff
-- 2026-11-13): per-project team and skills, answers to the questions
-- that change the plan, confirmed effort estimates, scope evidence,
-- plan versions and change requests (step 7).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Team on this project: who works on it and how many hours a week they
-- can give it. The org-level employees.weekly_capacity_hours stays the
-- default when someone is added.
-- ---------------------------------------------------------------------
create table public.project_team_members (
  project_id      uuid not null references public.projects (id) on delete cascade,
  employee_id     uuid not null references public.employees (id) on delete cascade,
  hours_per_week  numeric(5,1) not null check (hours_per_week between 1 and 80),
  role_title      text check (char_length(role_title) <= 120),
  created_at      timestamptz not null default now(),
  primary key (project_id, employee_id)
);
create index project_team_members_employee_idx on public.project_team_members (employee_id);

create trigger project_team_members_audit
  after insert or update or delete on public.project_team_members
  for each row execute function private.audit_row_change();

-- Skills this project needs; required = Must have, otherwise optional.
create table public.project_skills (
  project_id  uuid not null references public.projects (id) on delete cascade,
  skill_id    uuid not null references public.skills (id) on delete cascade,
  required    boolean not null default true,
  created_at  timestamptz not null default now(),
  primary key (project_id, skill_id)
);
create index project_skills_skill_idx on public.project_skills (skill_id);

create trigger project_skills_audit
  after insert or update or delete on public.project_skills
  for each row execute function private.audit_row_change();

-- ---------------------------------------------------------------------
-- Questions that change the plan (null = not answered yet).
-- ---------------------------------------------------------------------
alter table public.constraint_sets
  add column specialist_answer text check (specialist_answer in ('yes', 'no', 'unknown')),
  add column deadline_answer   text check (deadline_answer in ('hard', 'soft', 'unknown')),
  add column specialist_rate_confirmed boolean not null default false;

comment on column public.constraint_sets.specialist_answer is
  'May an outside specialist cover a required skill nobody on the team has? unknown = assume no.';
comment on column public.constraint_sets.deadline_answer is
  'Is must_finish_by a hard deadline? unknown = assume hard.';

-- ---------------------------------------------------------------------
-- Effort estimates: an AI estimate is not used until a person confirms it.
-- ---------------------------------------------------------------------
alter table public.wbs_nodes
  add column estimate_confirmed_at timestamptz,
  add column estimate_confirmed_by uuid references auth.users (id) on delete set null;
create index wbs_nodes_estimate_confirmed_by_idx on public.wbs_nodes (estimate_confirmed_by);

-- ---------------------------------------------------------------------
-- Scope evidence ("never invent"): stated = the quote says it; inferred =
-- derived from the quote (note explains); flagged = adds something the
-- source does not say (suggested_text is the version the source supports).
-- ---------------------------------------------------------------------
alter table public.scope_items
  add column evidence       text check (evidence in ('stated', 'inferred', 'flagged')),
  add column evidence_note  text check (char_length(evidence_note) <= 1000),
  add column suggested_text text check (char_length(suggested_text) between 1 and 2000);

-- ---------------------------------------------------------------------
-- Plan versions: an applied run becomes Plan v1, v2, ... of its project.
-- options records how the run was set up (chosen remedy, overrides).
-- ---------------------------------------------------------------------
alter table public.optimization_runs
  add column version integer check (version >= 1),
  add column options jsonb not null default '{}'::jsonb;
create unique index optimization_runs_project_version_idx
  on public.optimization_runs (project_id, version) where version is not null;
create index optimization_runs_applied_by_idx on public.optimization_runs (applied_by);
create index optimization_runs_requested_by_idx on public.optimization_runs (requested_by);

-- ---------------------------------------------------------------------
-- Changes & Versions (step 7): something changed after a plan was applied.
-- ---------------------------------------------------------------------
create table public.change_requests (
  id               uuid primary key default gen_random_uuid(),
  project_id       uuid not null references public.projects (id) on delete cascade,
  source           text not null check (source in ('notes', 'edits', 'progress')),
  input_text       text check (char_length(input_text) <= 50000),
  status           text not null default 'draft'
                   check (status in ('draft', 'analyzing', 'ready', 'replanning', 'proposed', 'approved', 'rejected', 'failed')),
  impact           jsonb,                          -- what changed, with quotes, and the questions to answer
  answers          jsonb not null default '{}'::jsonb,
  base_run_id      uuid references public.optimization_runs (id) on delete set null,
  proposed_run_id  uuid references public.optimization_runs (id) on delete set null,
  error            text,
  requested_by     uuid references auth.users (id) on delete set null default auth.uid(),
  created_at       timestamptz not null default now(),
  decided_at       timestamptz
);
create index change_requests_project_idx on public.change_requests (project_id, created_at desc);
create index change_requests_base_run_idx on public.change_requests (base_run_id);
create index change_requests_proposed_run_idx on public.change_requests (proposed_run_id);
create index change_requests_requested_by_idx on public.change_requests (requested_by);

create trigger change_requests_audit
  after insert or update or delete on public.change_requests
  for each row execute function private.audit_row_change();

-- ---------------------------------------------------------------------
-- RLS: same four project-scoped policies as the planning domain.
-- ---------------------------------------------------------------------
alter table public.project_team_members enable row level security;
alter table public.project_skills       enable row level security;
alter table public.change_requests      enable row level security;

do $$
declare
  t text;
begin
  foreach t in array array['project_team_members', 'project_skills', 'change_requests'] loop
    execute format(
      'create policy "%1$s: project readers can read" on public.%1$I for select to authenticated
         using ((select private.can_read_project(project_id)))', t);
    execute format(
      'create policy "%1$s: project writers can insert" on public.%1$I for insert to authenticated
         with check ((select private.can_write_project(project_id)))', t);
    execute format(
      'create policy "%1$s: project writers can update" on public.%1$I for update to authenticated
         using ((select private.can_write_project(project_id)))
         with check ((select private.can_write_project(project_id)))', t);
    execute format(
      'create policy "%1$s: project writers can delete" on public.%1$I for delete to authenticated
         using ((select private.can_write_project(project_id)))', t);
    execute format('revoke all on public.%I from anon', t);
  end loop;
end;
$$;
