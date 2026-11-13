-- pgTAP: per-project team, skills and change requests follow project
-- access (writers change them, viewers read, other tenants see nothing).
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;

select plan(14);

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values
  ('00000000-0000-0000-0000-0000000009a1', 'mia@example.com', '{"full_name":"Mia Owner"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000009b1', 'olaf@example.com', '{"full_name":"Olaf Other"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000009c1', 'vik@example.com', '{"full_name":"Vik Viewer"}', 'authenticated', 'authenticated');

create temp table mi_ids as
select (select org_id from public.organization_members where user_id = '00000000-0000-0000-0000-0000000009a1') as org_a;
grant select on mi_ids to authenticated;

insert into public.organization_members (org_id, user_id, role)
select org_a, '00000000-0000-0000-0000-0000000009c1', 'viewer' from mi_ids;

insert into public.projects (id, org_id, name, created_by)
select '79000000-0000-0000-0000-000000000001', org_a, 'Inputs Project', '00000000-0000-0000-0000-0000000009a1' from mi_ids;
insert into public.employees (id, org_id, full_name)
select '79100000-0000-0000-0000-000000000001', org_a, 'Sara Kim' from mi_ids;
insert into public.skills (id, org_id, name)
select '79200000-0000-0000-0000-000000000001', org_a, 'SAP Integration' from mi_ids;

select is(
  (select bool_and(relrowsecurity) from pg_class
   where oid in ('public.project_team_members'::regclass, 'public.project_skills'::regclass,
                 'public.change_requests'::regclass)),
  true, 'new tables have RLS enabled');
select is(
  has_table_privilege('anon', 'public.change_requests', 'select'),
  false, 'anon has no access to change_requests');

-- Owner ---------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000009a1","role":"authenticated"}';

select lives_ok(
  $$ insert into public.project_team_members (project_id, employee_id, hours_per_week)
     values ('79000000-0000-0000-0000-000000000001', '79100000-0000-0000-0000-000000000001', 32) $$,
  'a project writer adds a team member');
select throws_ok(
  $$ insert into public.project_team_members (project_id, employee_id, hours_per_week)
     values ('79000000-0000-0000-0000-000000000001', '79100000-0000-0000-0000-000000000001', 81) $$,
  '23514', null, 'hours per week stay within 1-80');
select lives_ok(
  $$ insert into public.project_skills (project_id, skill_id, required)
     values ('79000000-0000-0000-0000-000000000001', '79200000-0000-0000-0000-000000000001', true) $$,
  'a project writer adds a required skill');
select lives_ok(
  $$ insert into public.change_requests (id, project_id, source, input_text)
     values ('79300000-0000-0000-0000-000000000001', '79000000-0000-0000-0000-000000000001', 'notes', 'Budget cut to $365K') $$,
  'a project writer records a change');
select is(
  (select requested_by from public.change_requests where id = '79300000-0000-0000-0000-000000000001'),
  '00000000-0000-0000-0000-0000000009a1'::uuid, 'requested_by defaults to the caller');
select throws_ok(
  $$ insert into public.change_requests (project_id, source) values ('79000000-0000-0000-0000-000000000001', 'gossip') $$,
  '23514', null, 'unknown change sources are rejected');

-- Viewer --------------------------------------------------------------
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000009c1","role":"authenticated"}';

select is((select count(*)::int from public.project_team_members), 1, 'a viewer reads the team');
select throws_ok(
  $$ insert into public.project_skills (project_id, skill_id)
     values ('79000000-0000-0000-0000-000000000001', '79200000-0000-0000-0000-000000000001') $$,
  '42501', null, 'a viewer cannot add skills');
update public.project_team_members set hours_per_week = 10;
select is((select hours_per_week::int from public.project_team_members), 32, 'a viewer cannot change hours');
delete from public.change_requests;
select is((select count(*)::int from public.change_requests), 1, 'a viewer cannot delete changes');

-- Other tenant --------------------------------------------------------
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000009b1","role":"authenticated"}';

select is((select count(*)::int from public.project_team_members), 0, 'another tenant sees no team');
select is((select count(*)::int from public.change_requests), 0, 'another tenant sees no changes');

select * from finish();
rollback;
