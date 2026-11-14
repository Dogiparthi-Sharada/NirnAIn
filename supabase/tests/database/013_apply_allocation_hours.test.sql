-- pgTAP: applying a plan books the hours planned in each week (round 3, R3).
-- Each working day a work package is scheduled on books its daily hours (the
-- weekly rate over the run's working days), so a one-day 8 h task books 8 h,
-- not a 40 h week. Runs stored before the timeline are covered by 006.
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;

select plan(4);

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values ('d0000000-0000-0000-0000-000000000001', 'hours-owner@example.com', '{}', 'authenticated', 'authenticated');

create temp table hours_org as
select org_id from public.organization_members
where user_id = 'd0000000-0000-0000-0000-000000000001' and role = 'owner';

insert into public.projects (id, org_id, name, created_by)
select 'd1000000-0000-0000-0000-000000000001'::uuid, org_id, 'Hours test', 'd0000000-0000-0000-0000-000000000001'::uuid
from hours_org;
insert into public.employees (id, org_id, full_name)
select 'd2000000-0000-0000-0000-000000000001'::uuid, org_id, 'Ada' from hours_org
union all
select 'd2000000-0000-0000-0000-000000000002'::uuid, org_id, 'Bob' from hours_org;

insert into public.wbs_nodes (id, project_id, level, name) values
  ('d3000000-0000-0000-0000-000000000000', 'd1000000-0000-0000-0000-000000000001', 1, 'Root');
insert into public.wbs_nodes (id, project_id, parent_id, level, name, duration_days) values
  ('d3000000-0000-0000-0000-00000000000a', 'd1000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-000000000000', 2, 'A', 2),
  ('d3000000-0000-0000-0000-00000000000b', 'd1000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-000000000000', 2, 'B', 3),
  ('d3000000-0000-0000-0000-00000000000c', 'd1000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-000000000000', 2, 'C', 1),
  ('d3000000-0000-0000-0000-00000000000d', 'd1000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-000000000000', 2, 'D', 1),
  ('d3000000-0000-0000-0000-00000000000e', 'd1000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-000000000000', 2, 'E', 3),
  ('d3000000-0000-0000-0000-00000000000f', 'd1000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-000000000000', 2, 'F (done)', 0);

-- Planning starts Monday 2027-01-04 (week 0), Monday to Friday.
-- A 16 h Mon-Tue, C 8 h Mon, B 24 h Wed-Fri, D 8 h the next Monday, E 12 h Fri-Tue
-- (over a weekend, 4 h a day), F finished (no hours left) in week 1.
insert into public.optimization_runs (id, project_id, status, input_snapshot, result) values (
  'd4000000-0000-0000-0000-000000000001', 'd1000000-0000-0000-0000-000000000001', 'optimal',
  '{"start_date": "2027-01-04", "constraints": {"working_days_per_week": 5}}',
  '{"schedule": [
     {"wbsNodeId": "d3000000-0000-0000-0000-00000000000a", "start": "2027-01-04", "end": "2027-01-05"},
     {"wbsNodeId": "d3000000-0000-0000-0000-00000000000b", "start": "2027-01-06", "end": "2027-01-08"},
     {"wbsNodeId": "d3000000-0000-0000-0000-00000000000c", "start": "2027-01-04", "end": "2027-01-04"},
     {"wbsNodeId": "d3000000-0000-0000-0000-00000000000d", "start": "2027-01-11", "end": "2027-01-11"},
     {"wbsNodeId": "d3000000-0000-0000-0000-00000000000e", "start": "2027-01-15", "end": "2027-01-19"},
     {"wbsNodeId": "d3000000-0000-0000-0000-00000000000f", "start": "2027-01-11", "end": "2027-01-12", "done": true}
   ]}'
);
insert into public.planned_assignments (run_id, wbs_node_id, employee_id, start_week, end_week, hours_per_week) values
  ('d4000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-00000000000a', 'd2000000-0000-0000-0000-000000000001', 0, 0, 40),
  ('d4000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-00000000000b', 'd2000000-0000-0000-0000-000000000002', 0, 0, 40),
  ('d4000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-00000000000c', 'd2000000-0000-0000-0000-000000000002', 0, 0, 40),
  ('d4000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-00000000000d', 'd2000000-0000-0000-0000-000000000001', 1, 1, 40),
  ('d4000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-00000000000e', 'd2000000-0000-0000-0000-000000000001', 1, 2, 20),
  ('d4000000-0000-0000-0000-000000000001', 'd3000000-0000-0000-0000-00000000000f', 'd2000000-0000-0000-0000-000000000002', 1, 1, 0);

set local role authenticated;
set local request.jwt.claims = '{"sub":"d0000000-0000-0000-0000-000000000001","role":"authenticated"}';
select lives_ok(
  $$ select public.apply_optimization_run('d4000000-0000-0000-0000-000000000001') $$,
  'the owner applies the plan');
reset role;

select results_eq(
  $$ select employee_id, week_start, hours from public.allocations
     where project_id = 'd1000000-0000-0000-0000-000000000001' order by employee_id, week_start $$,
  $$ values
     ('d2000000-0000-0000-0000-000000000001'::uuid, '2027-01-04'::date, 16.0::numeric(5,1)),
     ('d2000000-0000-0000-0000-000000000001'::uuid, '2027-01-11'::date, 12.0::numeric(5,1)),
     ('d2000000-0000-0000-0000-000000000001'::uuid, '2027-01-18'::date, 8.0::numeric(5,1)),
     ('d2000000-0000-0000-0000-000000000002'::uuid, '2027-01-04'::date, 32.0::numeric(5,1)) $$,
  'each week books the hours planned in it: partial weeks are not booked in full');
select is(
  (select sum(hours) from public.allocations where project_id = 'd1000000-0000-0000-0000-000000000001'),
  68.0::numeric,
  'the booked hours add up to the planned effort (16 + 24 + 8 + 8 + 12 h)');
select is_empty(
  $$ select 1 from public.allocations
     where project_id = 'd1000000-0000-0000-0000-000000000001'
       and employee_id = 'd2000000-0000-0000-0000-000000000002' and week_start = '2027-01-11' $$,
  'finished work books no hours');

select * from finish();
rollback;
