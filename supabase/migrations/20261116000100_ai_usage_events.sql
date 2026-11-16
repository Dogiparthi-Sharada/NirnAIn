-- =====================================================================
-- What every Claude call cost: one row per successful call (bug 6).
--
-- Intake runs and AI jobs kept their tokens on their own rows, but reading
-- a project draft, finding what changed on step 7 and the planning
-- assistant recorded nothing, so the cost of a session could not be told.
-- Now the backend writes one row per successful Claude call, whichever
-- feature made it, with the model and its input and output tokens.
--
--   feature : project_draft | intake | wbs_generate | wbs_validate |
--             change_detection | assistant
--   org_id  : the workspace the call was made for (always known)
--   project_id / user_id : when the call was about a project / for a user
--
-- Only the backend writes rows, on its service connection: users get a
-- read policy (members of the workspace) and no insert, update or delete
-- policy. Rows go with their workspace or project.
-- =====================================================================

create table public.ai_usage_events (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null references public.organizations (id) on delete cascade,
  project_id     uuid references public.projects (id) on delete cascade,
  user_id        uuid references auth.users (id) on delete set null,
  feature        text not null check (feature in (
                   'project_draft', 'intake', 'wbs_generate', 'wbs_validate',
                   'change_detection', 'assistant')),
  model          text not null check (char_length(model) between 1 and 200),
  input_tokens   integer not null check (input_tokens >= 0),
  output_tokens  integer not null check (output_tokens >= 0),
  created_at     timestamptz not null default now()
);

comment on table public.ai_usage_events is
  'Tokens of every successful Claude call, by feature and model. Written by the backend only; workspace members read.';

create index ai_usage_events_org_created_idx on public.ai_usage_events (org_id, created_at desc);
create index ai_usage_events_project_idx on public.ai_usage_events (project_id);
create index ai_usage_events_user_idx on public.ai_usage_events (user_id);

alter table public.ai_usage_events enable row level security;

create policy "ai_usage_events: org members can read"
  on public.ai_usage_events for select to authenticated
  using ((select private.is_org_member(org_id)));

revoke all on public.ai_usage_events from anon;
revoke insert, update, delete, truncate on public.ai_usage_events from authenticated;
