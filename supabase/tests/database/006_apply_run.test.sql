-- pgTAP: public.apply_optimization_run turns a run's plan into allocations and owners.
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;

select plan(17);

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values
  ('c0000000-0000-0000-0000-000000000001', 'apply-owner@example.com', '{}', 'authenticated', 'authenticated'),
  ('c0000000-0000-0000-0000-000000000002', 'apply-viewer@example.com', '{}', 'authenticated', 'authenticated'),
  ('c0000000-0000-0000-0000-000000000003', 'apply-outsider@example.com', '{}', 'authenticated', 'authenticated');

create temp table apply_org as
select org_id from public.organization_members
where user_id = 'c0000000-0000-0000-0000-000000000001' and role = 'owner';
insert into public.organization_members (org_id, user_id, role)
select org_id, 'c0000000-0000-0000-0000-000000000002', 'viewer' from apply_org;

insert into public.projects (id, org_id, name, created_by)
select 'c1000000-0000-0000-0000-000000000001'::uuid, org_id, 'Apply test', 'c0000000-0000-0000-0000-000000000001'::uuid from apply_org
union all
select 'c1000000-0000-0000-0000-000000000002'::uuid, org_id, 'Other project', 'c0000000-0000-0000-0000-000000000001'::uuid from apply_org;
insert into public.employees (id, org_id, full_name)
select 'c2000000-0000-0000-0000-000000000001'::uuid, org_id, 'Ada' from apply_org
union all
select 'c2000000-0000-0000-0000-000000000002'::uuid, org_id, 'Bob' from apply_org;

insert into public.wbs_nodes (id, project_id, level, name) values
  ('c3000000-0000-0000-0000-000000000000', 'c1000000-0000-0000-0000-000000000001', 1, 'Root');
insert into public.wbs_nodes (id, project_id, parent_id, level, name, duration_days, owner_employee_id) values
  ('c3000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000001', 'c3000000-0000-0000-0000-000000000000', 2, 'Design', 10, null),
  ('c3000000-0000-0000-0000-000000000002', 'c1000000-0000-0000-0000-000000000001', 'c3000000-0000-0000-0000-000000000000', 2, 'Build', 5, 'c2000000-0000-0000-0000-000000000001'),
  ('c3000000-0000-0000-0000-000000000003', 'c1000000-0000-0000-0000-000000000001', 'c3000000-0000-0000-0000-000000000000', 2, 'Review', 5, null),
  ('c3000000-0000-0000-0000-000000000004', 'c1000000-0000-0000-0000-000000000001', 'c3000000-0000-0000-0000-000000000000', 2, 'Data', 5, 'c2000000-0000-0000-0000-000000000002');

-- Planning start is a Wednesday: week 0 starts Monday 2026-11-09.
insert into public.optimization_runs (id, project_id, status, input_snapshot, completion_date) values
  ('c4000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000001', 'optimal', '{"start_date": "2026-11-11"}', '2026-11-27'),
  ('c4000000-0000-0000-0000-000000000002', 'c1000000-0000-0000-0000-000000000001', 'infeasible', '{"start_date": "2026-11-11"}', null),
  ('c4000000-0000-0000-0000-000000000003', 'c1000000-0000-0000-0000-000000000001', 'feasible', '{"start_date": "2026-11-11"}', null);
insert into public.planned_assignments (run_id, wbs_node_id, employee_id, contractor_label, start_week, end_week, hours_per_week) values
  ('c4000000-0000-0000-0000-000000000001', 'c3000000-0000-0000-0000-000000000001', 'c2000000-0000-0000-0000-000000000001', null, 0, 1, 30),
  ('c4000000-0000-0000-0000-000000000001', 'c3000000-0000-0000-0000-000000000002', 'c2000000-0000-0000-0000-000000000002', null, 2, 2, 20),
  ('c4000000-0000-0000-0000-000000000001', 'c3000000-0000-0000-0000-000000000003', 'c2000000-0000-0000-0000-000000000001', null, 1, 1, 60),
  ('c4000000-0000-0000-0000-000000000001', 'c3000000-0000-0000-0000-000000000004', null, 'Contractor', 0, 0, 40);

insert into public.allocations (employee_id, project_id, week_start, hours) values
  ('c2000000-0000-0000-0000-000000000002', 'c1000000-0000-0000-0000-000000000001', '2026-11-09', 40),
  ('c2000000-0000-0000-0000-000000000001', 'c1000000-0000-0000-0000-000000000002', '2026-11-09', 10);

select ok(
  (select prosecdef from pg_proc where oid = 'public.apply_optimization_run(uuid)'::regprocedure),
  'apply_optimization_run is security definer');
select ok(
  not has_function_privilege('anon', 'public.apply_optimization_run(uuid)', 'execute')
  and has_function_privilege('authenticated', 'public.apply_optimization_run(uuid)', 'execute'),
  'only authenticated users can execute apply_optimization_run');

set local role authenticated;
set local request.jwt.claims = '{"sub":"c0000000-0000-0000-0000-000000000002","role":"authenticated"}';
select throws_ok(
  $$ select public.apply_optimization_run('c4000000-0000-0000-0000-000000000001') $$,
  '42501', null, 'a viewer cannot apply a run');

set local request.jwt.claims = '{"sub":"c0000000-0000-0000-0000-000000000003","role":"authenticated"}';
select throws_ok(
  $$ select public.apply_optimization_run('c4000000-0000-0000-0000-000000000001') $$,
  '42501', null, 'another tenant cannot apply a run');

set local request.jwt.claims = '{"sub":"c0000000-0000-0000-0000-000000000001","role":"authenticated"}';
select throws_ok(
  $$ select public.apply_optimization_run('c4000000-0000-0000-0000-000000000002') $$,
  'P0001', 'Only a successful run can be applied', 'an infeasible run cannot be applied');
select throws_ok(
  $$ select public.apply_optimization_run('c4000000-0000-0000-0000-0000000000ff') $$,
  '42501', null, 'a missing run is rejected');
select lives_ok(
  $$ select public.apply_optimization_run('c4000000-0000-0000-0000-000000000001') $$,
  'a project writer can apply a successful run');
reset role;

select results_eq(
  $$ select employee_id, week_start, hours, run_id from public.allocations
     where project_id = 'c1000000-0000-0000-0000-000000000001' order by employee_id, week_start $$,
  $$ values
     ('c2000000-0000-0000-0000-000000000001'::uuid, '2026-11-09'::date, 30.0::numeric(5,1), 'c4000000-0000-0000-0000-000000000001'::uuid),
     ('c2000000-0000-0000-0000-000000000001'::uuid, '2026-11-16'::date, 80.0::numeric(5,1), 'c4000000-0000-0000-0000-000000000001'::uuid),
     ('c2000000-0000-0000-0000-000000000002'::uuid, '2026-11-23'::date, 20.0::numeric(5,1), 'c4000000-0000-0000-0000-000000000001'::uuid) $$,
  'allocations are replaced by weekly employee hours, summed and capped at 80');
select is(
  (select hours from public.allocations where project_id = 'c1000000-0000-0000-0000-000000000002'),
  10.0::numeric(5,1), 'other projects'' allocations are kept');
select results_eq(
  $$ select id, owner_employee_id from public.wbs_nodes
     where project_id = 'c1000000-0000-0000-0000-000000000001' and level = 2 order by id $$,
  $$ values
     ('c3000000-0000-0000-0000-000000000001'::uuid, 'c2000000-0000-0000-0000-000000000001'::uuid),
     ('c3000000-0000-0000-0000-000000000002'::uuid, 'c2000000-0000-0000-0000-000000000002'::uuid),
     ('c3000000-0000-0000-0000-000000000003'::uuid, 'c2000000-0000-0000-0000-000000000001'::uuid),
     ('c3000000-0000-0000-0000-000000000004'::uuid, 'c2000000-0000-0000-0000-000000000002'::uuid) $$,
  'assigned work packages get their planned employee as owner; contractor tasks are unchanged');
select ok(
  (select applied_at is not null and applied_by = 'c0000000-0000-0000-0000-000000000001' and version = 1
   from public.optimization_runs where id = 'c4000000-0000-0000-0000-000000000001'),
  'the run records who applied it and when, as plan v1');
select ok(
  (select bool_and(applied_at is null and version is null) from public.optimization_runs
   where id in ('c4000000-0000-0000-0000-000000000002', 'c4000000-0000-0000-0000-000000000003')),
  'other runs are not marked applied');
select is(
  (select current_step::text || '/' || status::text from public.projects
   where id = 'c1000000-0000-0000-0000-000000000001'),
  'done/on_track', 'the project moves to done and on track');

set local role authenticated;
set local request.jwt.claims = '{"sub":"c0000000-0000-0000-0000-000000000001","role":"authenticated"}';
select throws_ok(
  $$ select public.apply_optimization_run('c4000000-0000-0000-0000-000000000001') $$,
  'P0001', 'This plan is already applied', 'a run cannot be applied twice');
select lives_ok(
  $$ select public.apply_optimization_run('c4000000-0000-0000-0000-000000000003') $$,
  'a later run can be applied on top of the current plan');
reset role;

select results_eq(
  $$ select id, version from public.optimization_runs
     where project_id = 'c1000000-0000-0000-0000-000000000001' and version is not null order by version $$,
  $$ values
     ('c4000000-0000-0000-0000-000000000001'::uuid, 1),
     ('c4000000-0000-0000-0000-000000000003'::uuid, 2) $$,
  'each applied run becomes the project''s next plan version');
select results_eq(
  $$ select user_id, org_id, project_id, level::text, message from public.notifications
     where project_id = 'c1000000-0000-0000-0000-000000000001' order by created_at, message $$,
  $$ select 'c0000000-0000-0000-0000-000000000001'::uuid, org_id, 'c1000000-0000-0000-0000-000000000001'::uuid,
            'info', 'Plan v1 applied for Apply test · finishes Nov 27, 2026' from apply_org
     union all
     select 'c0000000-0000-0000-0000-000000000001'::uuid, org_id, 'c1000000-0000-0000-0000-000000000001'::uuid,
            'info', 'Plan v2 applied for Apply test' from apply_org $$,
  'the caller is notified of each applied plan version');

select * from finish();
rollback;
