-- =====================================================================
-- Apply an optimization run: turn the solver's plan into committed
-- allocations and WBS owners.
--
-- allocations are not writable by users (RLS has no write policies), so
-- this security definer function does the writes after checking that the
-- caller can write the run's project. Week indexes in planned_assignments
-- are 0-based calendar weeks counted from the Monday of the week that
-- contains the run's planning start (input_snapshot.start_date), the same
-- mapping as backend/app/optimization/calendar.py:week_index. end_week is
-- inclusive.
-- =====================================================================

create or replace function public.apply_optimization_run(p_run_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_run    public.optimization_runs%rowtype;
  v_start  date;
  v_monday date;
begin
  select * into v_run from public.optimization_runs where id = p_run_id for update;
  if v_run.id is null or not (select private.can_write_project(v_run.project_id)) then
    raise exception 'You do not have permission to apply this run' using errcode = '42501';
  end if;
  if v_run.status not in ('optimal', 'feasible') then
    raise exception 'Only a successful run can be applied' using errcode = 'P0001';
  end if;

  v_start := coalesce((v_run.input_snapshot ->> 'start_date')::date, v_run.created_at::date);
  v_monday := v_start - (extract(isodow from v_start)::int - 1);

  delete from public.allocations where project_id = v_run.project_id;

  insert into public.allocations (employee_id, project_id, week_start, hours, run_id)
  select pa.employee_id,
         v_run.project_id,
         v_monday + 7 * w.week,
         least(sum(pa.hours_per_week), 80),
         p_run_id
  from public.planned_assignments pa
  cross join lateral generate_series(pa.start_week, pa.end_week) as w(week)
  where pa.run_id = p_run_id
    and pa.employee_id is not null
  group by pa.employee_id, w.week;

  update public.wbs_nodes n
  set owner_employee_id = pa.employee_id
  from public.planned_assignments pa
  where pa.run_id = p_run_id
    and pa.employee_id is not null
    and n.id = pa.wbs_node_id
    and n.project_id = v_run.project_id
    and n.owner_employee_id is distinct from pa.employee_id;

  update public.optimization_runs
  set applied_at = now(), applied_by = (select auth.uid())
  where id = p_run_id;

  update public.projects
  set current_step = 'done', status = 'on_track'
  where id = v_run.project_id;
end;
$$;

comment on function public.apply_optimization_run(uuid) is
  'Applies a successful optimization run: replaces the project''s allocations with the plan''s weekly hours (capped at 80), sets WBS owners, marks the run applied and the project on track.';

revoke execute on function public.apply_optimization_run(uuid) from public, anon;
grant execute on function public.apply_optimization_run(uuid) to authenticated;
