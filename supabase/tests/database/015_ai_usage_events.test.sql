-- pgTAP: ai_usage_events RLS (workspace members read, nobody but the
-- backend writes), its checks and its cascades.
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;

select plan(13);

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values
  ('00000000-0000-0000-0000-0000000000a1', 'aija@example.com', '{"full_name":"Aija Owner"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000b1', 'bruno@example.com', '{"full_name":"Bruno Other"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000c1', 'vera@example.com', '{"full_name":"Vera Viewer"}', 'authenticated', 'authenticated');

-- Every sign-up gets its own workspace (trigger); Vera also joins Aija's as a viewer.
create temp table usage_ids as
select
  (select org_id from public.organization_members where user_id = '00000000-0000-0000-0000-0000000000a1') as org_a,
  (select org_id from public.organization_members where user_id = '00000000-0000-0000-0000-0000000000b1') as org_b;
grant select on usage_ids to authenticated;

insert into public.organization_members (org_id, user_id, role)
select org_a, '00000000-0000-0000-0000-0000000000c1', 'viewer' from usage_ids;

insert into public.projects (id, org_id, name, created_by)
select '71000000-0000-0000-0000-000000000001', org_a, 'Usage Project', '00000000-0000-0000-0000-0000000000a1' from usage_ids;

select is(
  (select relrowsecurity from pg_class where oid = 'public.ai_usage_events'::regclass),
  true, 'ai_usage_events has RLS enabled');

select is(
  has_table_privilege('anon', 'public.ai_usage_events', 'select'),
  false, 'anon has no access to ai_usage_events');

-- ---------------------------------------------------------------------
-- The backend (service connection) records calls
-- ---------------------------------------------------------------------
select lives_ok(
  $$ insert into public.ai_usage_events (org_id, project_id, user_id, feature, model, input_tokens, output_tokens)
     select org_a, '71000000-0000-0000-0000-000000000001'::uuid, '00000000-0000-0000-0000-0000000000a1'::uuid,
            'intake', 'claude-sonnet-5-5', 1950, 2392 from usage_ids
     union all
     select org_a, null::uuid, '00000000-0000-0000-0000-0000000000a1'::uuid,
            'project_draft', 'claude-sonnet-5-5', 300, 120 from usage_ids
     union all
     select org_b, null::uuid, '00000000-0000-0000-0000-0000000000b1'::uuid,
            'assistant', 'claude-sonnet-5-5', 800, 90 from usage_ids $$,
  'the service connection can record usage');

select throws_ok(
  $$ insert into public.ai_usage_events (org_id, feature, model, input_tokens, output_tokens)
     select org_a, 'translation', 'claude-sonnet-5-5', 1, 1 from usage_ids $$,
  '23514', null, 'only the known features are accepted');

select throws_ok(
  $$ insert into public.ai_usage_events (org_id, feature, model, input_tokens, output_tokens)
     select org_a, 'assistant', 'claude-sonnet-5-5', -1, 1 from usage_ids $$,
  '23514', null, 'token counts cannot be negative');

-- ---------------------------------------------------------------------
-- Owner of workspace A
-- ---------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated"}';

select is(
  (select count(*)::int from public.ai_usage_events),
  2, 'a member reads their workspace''s usage only');

select is(
  (select sum(input_tokens + output_tokens)::int from public.ai_usage_events where feature = 'intake'),
  4342, 'a member reads the tokens of each call');

select throws_ok(
  $$ insert into public.ai_usage_events (org_id, feature, model, input_tokens, output_tokens)
     select org_a, 'assistant', 'claude-sonnet-5-5', 1, 1 from usage_ids $$,
  '42501', null, 'an authenticated user cannot record usage');

select throws_ok(
  $$ update public.ai_usage_events set input_tokens = 0 $$,
  '42501', null, 'an authenticated user cannot change usage');

-- ---------------------------------------------------------------------
-- Viewer of workspace A, and another workspace
-- ---------------------------------------------------------------------
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000c1","role":"authenticated"}';

select is(
  (select count(*)::int from public.ai_usage_events where org_id = (select org_a from usage_ids)),
  2, 'a viewer of the workspace reads its usage too');

set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000b1","role":"authenticated"}';

select is(
  (select count(*)::int from public.ai_usage_events where org_id = (select org_a from usage_ids)),
  0, 'another workspace cannot read the usage');

select is(
  (select count(*)::int from public.ai_usage_events),
  1, 'another workspace reads only its own usage');

-- ---------------------------------------------------------------------
-- Rows go with their project
-- ---------------------------------------------------------------------
reset role;
delete from public.projects where id = '71000000-0000-0000-0000-000000000001';
select is(
  (select count(*)::int from public.ai_usage_events where org_id = (select org_a from usage_ids)),
  1, 'a project''s usage is deleted with it; the workspace''s other usage stays');

select * from finish();
rollback;
