-- pgTAP: ai_jobs RLS (tenant isolation, queue-only writes) and the
-- one-active-job / one-active-intake-run unique indexes.
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;

select plan(16);

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values
  ('00000000-0000-0000-0000-0000000000a1', 'aija@example.com', '{"full_name":"Aija Owner"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000b1', 'bruno@example.com', '{"full_name":"Bruno Other"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000c1', 'vera@example.com', '{"full_name":"Vera Viewer"}', 'authenticated', 'authenticated');

create temp table ai_ids as
select (select org_id from public.organization_members where user_id = '00000000-0000-0000-0000-0000000000a1') as org_a;
grant select on ai_ids to authenticated;

insert into public.organization_members (org_id, user_id, role)
select org_a, '00000000-0000-0000-0000-0000000000c1', 'viewer' from ai_ids;

insert into public.projects (id, org_id, name, created_by)
select '70000000-0000-0000-0000-000000000001', org_a, 'AI Project', '00000000-0000-0000-0000-0000000000a1' from ai_ids;

select is(
  (select relrowsecurity from pg_class where oid = 'public.ai_jobs'::regclass),
  true, 'ai_jobs has RLS enabled');

select is(
  has_table_privilege('anon', 'public.ai_jobs', 'select'),
  false, 'anon has no access to ai_jobs');

-- ---------------------------------------------------------------------
-- Owner of the project's org
-- ---------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated"}';

select lives_ok(
  $$ insert into public.ai_jobs (id, project_id, kind)
     values ('80000000-0000-0000-0000-000000000001', '70000000-0000-0000-0000-000000000001', 'wbs_generate') $$,
  'a project writer can queue an AI job');

select is(
  (select requested_by from public.ai_jobs where id = '80000000-0000-0000-0000-000000000001'),
  '00000000-0000-0000-0000-0000000000a1'::uuid, 'requested_by defaults to the caller');

select throws_ok(
  $$ insert into public.ai_jobs (project_id, kind)
     values ('70000000-0000-0000-0000-000000000001', 'wbs_generate') $$,
  '23505', null, 'only one active job per project and kind');

select lives_ok(
  $$ insert into public.ai_jobs (project_id, kind)
     values ('70000000-0000-0000-0000-000000000001', 'wbs_validate') $$,
  'a job of another kind can run alongside');

select throws_ok(
  $$ insert into public.ai_jobs (project_id, kind, status)
     values ('70000000-0000-0000-0000-000000000001', 'wbs_generate', 'succeeded') $$,
  '42501', null, 'users can only queue jobs, not insert finished ones');

update public.ai_jobs set status = 'succeeded', result = '{"forged":true}'
where id = '80000000-0000-0000-0000-000000000001';
select is(
  (select status::text from public.ai_jobs where id = '80000000-0000-0000-0000-000000000001'),
  'queued', 'users cannot update jobs (the worker does)');

delete from public.ai_jobs where id = '80000000-0000-0000-0000-000000000001';
select is(
  (select count(*)::int from public.ai_jobs where id = '80000000-0000-0000-0000-000000000001'),
  1, 'users cannot delete jobs');

insert into public.intake_runs (project_id) values ('70000000-0000-0000-0000-000000000001');
select throws_ok(
  $$ insert into public.intake_runs (project_id) values ('70000000-0000-0000-0000-000000000001') $$,
  '23505', null, 'only one active intake run per project');

-- ---------------------------------------------------------------------
-- Viewer in the same org
-- ---------------------------------------------------------------------
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000c1","role":"authenticated"}';

select is(
  (select count(*)::int from public.ai_jobs where project_id = '70000000-0000-0000-0000-000000000001'),
  2, 'a viewer can read the project''s jobs');

select throws_ok(
  $$ insert into public.ai_jobs (project_id, kind)
     values ('70000000-0000-0000-0000-000000000001', 'wbs_generate') $$,
  '42501', null, 'a viewer cannot queue jobs');

-- ---------------------------------------------------------------------
-- Another tenant
-- ---------------------------------------------------------------------
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000b1","role":"authenticated"}';

select is(
  (select count(*)::int from public.ai_jobs),
  0, 'another tenant cannot see the jobs');

select throws_ok(
  $$ insert into public.ai_jobs (project_id, kind)
     values ('70000000-0000-0000-0000-000000000001', 'wbs_validate') $$,
  '42501', null, 'another tenant cannot queue jobs on the project');

-- ---------------------------------------------------------------------
-- Worker (service connection) finishes the job; a new one can be queued
-- ---------------------------------------------------------------------
reset role;
update public.ai_jobs set status = 'succeeded', finished_at = now()
where id = '80000000-0000-0000-0000-000000000001';

set local role authenticated;
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated"}';

select lives_ok(
  $$ insert into public.ai_jobs (project_id, kind)
     values ('70000000-0000-0000-0000-000000000001', 'wbs_generate') $$,
  'a new job can be queued once the previous one finished');

reset role;
delete from public.projects where id = '70000000-0000-0000-0000-000000000001';
select is(
  (select count(*)::int from public.ai_jobs where project_id = '70000000-0000-0000-0000-000000000001'),
  0, 'jobs are deleted with their project');

select * from finish();
rollback;
