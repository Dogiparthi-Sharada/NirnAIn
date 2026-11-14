-- Links between rows stay inside one project / organization, whatever RLS hides
-- (SEC-001, migration 20261114000100_cross_project_links).
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;
select plan(16);

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values
  ('c0000000-0000-0000-0000-00000000000a', 'links-a@example.com', '{}', 'authenticated', 'authenticated'),
  ('c0000000-0000-0000-0000-00000000000b', 'links-b@example.com', '{}', 'authenticated', 'authenticated');

create temp table links_orgs as
select member.user_id, member.org_id from public.organization_members member
where member.user_id in ('c0000000-0000-0000-0000-00000000000a', 'c0000000-0000-0000-0000-00000000000b')
  and member.role = 'owner';
grant select on links_orgs to authenticated;

-- Workspace A: project PA (packages A1, A2), project PA2 (package A3), an employee,
-- a skill and a scope item.
set local role authenticated;
set local request.jwt.claims = '{"sub":"c0000000-0000-0000-0000-00000000000a","role":"authenticated"}';
insert into public.projects (id, org_id, name)
select 'c1000000-0000-0000-0000-0000000000a1', org_id, 'Project A' from links_orgs
where user_id = 'c0000000-0000-0000-0000-00000000000a';
insert into public.projects (id, org_id, name)
select 'c1000000-0000-0000-0000-0000000000a2', org_id, 'Project A two' from links_orgs
where user_id = 'c0000000-0000-0000-0000-00000000000a';
insert into public.wbs_nodes (id, project_id, parent_id, level, name) values
  ('c2000000-0000-0000-0000-0000000000a0', 'c1000000-0000-0000-0000-0000000000a1', null, 1, 'Root A'),
  ('c2000000-0000-0000-0000-0000000000a1', 'c1000000-0000-0000-0000-0000000000a1', 'c2000000-0000-0000-0000-0000000000a0', 2, 'A1'),
  ('c2000000-0000-0000-0000-0000000000a2', 'c1000000-0000-0000-0000-0000000000a1', 'c2000000-0000-0000-0000-0000000000a0', 2, 'A2'),
  ('c2000000-0000-0000-0000-0000000000b0', 'c1000000-0000-0000-0000-0000000000a2', null, 1, 'Root A two'),
  ('c2000000-0000-0000-0000-0000000000a3', 'c1000000-0000-0000-0000-0000000000a2', 'c2000000-0000-0000-0000-0000000000b0', 2, 'A3');
insert into public.employees (id, org_id, full_name)
select 'c3000000-0000-0000-0000-0000000000a1', org_id, 'Alice Employee' from links_orgs
where user_id = 'c0000000-0000-0000-0000-00000000000a';
insert into public.skills (id, org_id, name)
select 'c4000000-0000-0000-0000-0000000000a1', org_id, 'Skill A' from links_orgs
where user_id = 'c0000000-0000-0000-0000-00000000000a';
insert into public.scope_items (id, project_id, category, text)
values ('c5000000-0000-0000-0000-0000000000a1', 'c1000000-0000-0000-0000-0000000000a1', 'deliverable', 'Scope A');

select throws_ok(
  $$ insert into public.dependencies (project_id, predecessor_id, successor_id)
     values ('c1000000-0000-0000-0000-0000000000a1', 'c2000000-0000-0000-0000-0000000000a1',
             'c2000000-0000-0000-0000-0000000000a3') $$,
  'P0001', 'Work package not found in this project',
  'a visible package of another project of the same workspace is refused');

-- Workspace B: project PB with package B1, B's own employee and skill.
set local request.jwt.claims = '{"sub":"c0000000-0000-0000-0000-00000000000b","role":"authenticated"}';
insert into public.projects (id, org_id, name)
select 'c1000000-0000-0000-0000-0000000000b1', org_id, 'Project B' from links_orgs
where user_id = 'c0000000-0000-0000-0000-00000000000b';
insert into public.wbs_nodes (id, project_id, parent_id, level, name) values
  ('c2000000-0000-0000-0000-0000000000c0', 'c1000000-0000-0000-0000-0000000000b1', null, 1, 'Root B'),
  ('c2000000-0000-0000-0000-0000000000b1', 'c1000000-0000-0000-0000-0000000000b1', 'c2000000-0000-0000-0000-0000000000c0', 2, 'B1');
insert into public.employees (id, org_id, full_name)
select 'c3000000-0000-0000-0000-0000000000b1', org_id, 'Bob Employee' from links_orgs
where user_id = 'c0000000-0000-0000-0000-00000000000b';

select throws_ok(
  $$ insert into public.dependencies (project_id, predecessor_id, successor_id)
     values ('c1000000-0000-0000-0000-0000000000b1', 'c2000000-0000-0000-0000-0000000000a1',
             'c2000000-0000-0000-0000-0000000000a2') $$,
  'P0001', 'Work package not found in this project',
  'another workspace cannot file a dependency between your packages');
select throws_ok(
  $$ insert into public.dependencies (project_id, predecessor_id, successor_id)
     values ('c1000000-0000-0000-0000-0000000000b1', 'c2000000-0000-0000-0000-0000000000a1',
             'c2000000-0000-0000-0000-0000000000b1') $$,
  'P0001', 'Work package not found in this project',
  'another workspace cannot link your package to its own');
select throws_ok(
  $$ insert into public.dependencies (project_id, predecessor_id, successor_id)
     values ('c1000000-0000-0000-0000-0000000000b1', 'c2000000-0000-0000-0000-0000000000ff',
             'c2000000-0000-0000-0000-0000000000b1') $$,
  'P0001', 'Work package not found in this project',
  'an unknown id gets the same answer (no existence oracle)');
select throws_ok(
  $$ insert into public.wbs_nodes (project_id, parent_id, level, name)
     values ('c1000000-0000-0000-0000-0000000000b1', 'c2000000-0000-0000-0000-0000000000a0', 2, 'Under A') $$,
  'P0001', 'WBS parent not found in this project', 'a hidden parent of another workspace is refused');
select throws_ok(
  $$ update public.wbs_nodes set owner_employee_id = 'c3000000-0000-0000-0000-0000000000a1'
     where id = 'c2000000-0000-0000-0000-0000000000b1' $$,
  'P0001', 'Employee not found in this organization', 'an owner of another workspace is refused');
select lives_ok(
  $$ update public.wbs_nodes set owner_employee_id = 'c3000000-0000-0000-0000-0000000000b1'
     where id = 'c2000000-0000-0000-0000-0000000000b1' $$,
  'an owner of the same workspace is accepted');
select throws_ok(
  $$ update public.wbs_nodes set scope_item_id = 'c5000000-0000-0000-0000-0000000000a1'
     where id = 'c2000000-0000-0000-0000-0000000000b1' $$,
  'P0001', 'Scope item not found in this project', 'a scope item of another workspace is refused');
select throws_ok(
  $$ insert into public.milestones (project_id, name, due_date, wbs_node_id)
     values ('c1000000-0000-0000-0000-0000000000b1', 'Go-live', '2027-01-29',
             'c2000000-0000-0000-0000-0000000000a1') $$,
  'P0001', 'Work package not found in this project', 'a milestone on another workspace''s package is refused');
select throws_ok(
  $$ insert into public.project_team_members (project_id, employee_id, hours_per_week)
     values ('c1000000-0000-0000-0000-0000000000b1', 'c3000000-0000-0000-0000-0000000000a1', 40) $$,
  'P0001', 'Employee not found in this organization', 'a team member of another workspace is refused');
select throws_ok(
  $$ insert into public.project_skills (project_id, skill_id)
     values ('c1000000-0000-0000-0000-0000000000b1', 'c4000000-0000-0000-0000-0000000000a1') $$,
  'P0001', 'Skill not found in this organization', 'a project skill of another workspace is refused');
select throws_ok(
  $$ insert into public.task_skill_requirements (wbs_node_id, skill_id)
     values ('c2000000-0000-0000-0000-0000000000b1', 'c4000000-0000-0000-0000-0000000000a1') $$,
  'P0001', 'Skill not found in this organization', 'a package skill of another workspace is refused');
select throws_ok(
  $$ insert into public.employee_skills (employee_id, skill_id)
     values ('c3000000-0000-0000-0000-0000000000b1', 'c4000000-0000-0000-0000-0000000000a1') $$,
  'P0001', 'Skill not found in this organization', 'an employee skill of another workspace is refused');

-- A's own link is not blocked by anything B tried.
set local request.jwt.claims = '{"sub":"c0000000-0000-0000-0000-00000000000a","role":"authenticated"}';
select lives_ok(
  $$ insert into public.dependencies (project_id, predecessor_id, successor_id)
     values ('c1000000-0000-0000-0000-0000000000a1', 'c2000000-0000-0000-0000-0000000000a1',
             'c2000000-0000-0000-0000-0000000000a2') $$,
  'the owner links their own packages');

reset role;
select ok(
  (select with_check from pg_policies
   where tablename = 'dependencies' and policyname = 'dependencies: project writers can insert')
    ~ 'successor_id',
  'the insert policy requires both packages in the project');
select ok(
  (select with_check from pg_policies
   where tablename = 'dependencies' and policyname = 'dependencies: project writers can update')
    ~ 'predecessor_id',
  'the update policy requires both packages in the project');
select * from finish();
rollback;
