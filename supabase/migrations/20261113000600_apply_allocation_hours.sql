-- =====================================================================
-- Applying a plan books the hours actually planned in each week
-- (round 3, R3).
--
-- 20261113000200 booked a work package's weekly rate (planned_assignments
-- .hours_per_week, its effort spread over the working days the plan gives
-- it, times the working days of a week) for every calendar week the package
-- touches. A package that works only part of a week was over-booked: a
-- one-day 8 h task booked a 40 h week, and other projects then planned
-- that person as busy.
--
-- Now each working day a package is scheduled on books its daily hours
-- (the weekly rate over the run's working days per week, from
-- input_snapshot.constraints.working_days_per_week, default 5). The days
-- come from the run's timeline (result.schedule: wbsNodeId, first and last
-- day, inclusive), written by the trusted optimizer worker, never by the
-- caller. Runs stored before the timeline existed keep the weekly rate for
-- every week the package touches, as before. Weekly totals are summed per
-- person, rounded to 0.1 h and capped at 80 (the allocations check); weeks
-- with no hours (finished work) are not booked.
--
-- Everything else is unchanged: permission and status checks, the next
-- plan version, WBS owners, the project status and the notification.
-- =====================================================================

create or replace function public.apply_optimization_run(p_run_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_run      public.optimization_runs%rowtype;
  v_project  public.projects%rowtype;
  v_version  integer;
  v_start    date;
  v_monday   date;
  v_per_week integer;
  v_schedule jsonb;
begin
  select * into v_run from public.optimization_runs where id = p_run_id for update;
  if v_run.id is null or not (select private.can_write_project(v_run.project_id)) then
    raise exception 'You do not have permission to apply this run' using errcode = '42501';
  end if;
  if v_run.status not in ('optimal', 'feasible') then
    raise exception 'Only a successful run can be applied' using errcode = 'P0001';
  end if;
  if v_run.version is not null or v_run.applied_at is not null then
    raise exception 'This plan is already applied' using errcode = 'P0001';
  end if;

  -- Locking the project serializes concurrent applies, so versions stay gapless.
  select * into v_project from public.projects where id = v_run.project_id for update;
  select coalesce(max(version), 0) + 1 into v_version
  from public.optimization_runs
  where project_id = v_run.project_id;

  v_start := coalesce((v_run.input_snapshot ->> 'start_date')::date, v_run.created_at::date);
  v_monday := v_start - (extract(isodow from v_start)::int - 1);
  -- Working days are the first n weekdays (5 = Mon-Fri), as in optimization/calendar.py.
  v_per_week := least(greatest(coalesce(
    (v_run.input_snapshot -> 'constraints' ->> 'working_days_per_week')::int, 5), 1), 7);
  v_schedule := case
    when jsonb_typeof(v_run.result -> 'schedule') = 'array' then v_run.result -> 'schedule'
    else '[]'::jsonb
  end;

  delete from public.allocations where project_id = v_run.project_id;

  with planned as (
    select pa.employee_id,
           pa.start_week,
           pa.end_week,
           pa.hours_per_week,
           (scheduled.item ->> 'start')::date as first_day,
           (scheduled.item ->> 'end')::date as last_day
    from public.planned_assignments pa
    left join lateral (
      select element as item
      from jsonb_array_elements(v_schedule) as element
      where element ->> 'wbsNodeId' = pa.wbs_node_id::text
      limit 1
    ) scheduled on true
    where pa.run_id = p_run_id
      and pa.employee_id is not null
  ),
  booked as (
    -- Each working day the package is scheduled on: its daily hours, in that day's week.
    select planned.employee_id,
           working.work_day - (extract(isodow from working.work_day)::int - 1) as week_start,
           planned.hours_per_week / v_per_week as hours
    from planned
    cross join lateral (
      select planned.first_day + offset_days.days as work_day
      from generate_series(0, planned.last_day - planned.first_day) as offset_days(days)
    ) working
    where planned.first_day is not null
      and extract(isodow from working.work_day) <= v_per_week
    union all
    -- Runs stored before the timeline: the weekly rate in every week the package touches.
    select planned.employee_id,
           v_monday + 7 * weeks.week,
           planned.hours_per_week
    from planned
    cross join lateral generate_series(planned.start_week, planned.end_week) as weeks(week)
    where planned.first_day is null
  )
  insert into public.allocations (employee_id, project_id, week_start, hours, run_id)
  select booked.employee_id,
         v_run.project_id,
         booked.week_start,
         least(round(sum(booked.hours), 1), 80),
         p_run_id
  from booked
  group by booked.employee_id, booked.week_start
  having round(sum(booked.hours), 1) > 0;

  update public.wbs_nodes node
  set owner_employee_id = pa.employee_id
  from public.planned_assignments pa
  where pa.run_id = p_run_id
    and pa.employee_id is not null
    and node.id = pa.wbs_node_id
    and node.project_id = v_run.project_id
    and node.owner_employee_id is distinct from pa.employee_id;

  update public.optimization_runs
  set applied_at = now(), applied_by = (select auth.uid()), version = v_version
  where id = p_run_id;

  update public.projects
  set current_step = 'done', status = 'on_track'
  where id = v_run.project_id;

  insert into public.notifications (user_id, org_id, project_id, level, message)
  values (
    (select auth.uid()),
    v_project.org_id,
    v_project.id,
    'info',
    left(
      format('Plan v%s applied for %s', v_version, v_project.name)
        || coalesce(' · finishes ' || to_char(v_run.completion_date, 'Mon FMDD, YYYY'), ''),
      500
    )
  );
end;
$$;

comment on function public.apply_optimization_run(uuid) is
  'Applies a successful optimization run once: replaces the project''s allocations with the hours the plan puts in each week (each scheduled working day books its daily hours; summed per person and week, capped at 80), sets WBS owners, makes the run the project''s next plan version, marks the project on track and notifies the caller.';

revoke execute on function public.apply_optimization_run(uuid) from public, anon;
grant execute on function public.apply_optimization_run(uuid) to authenticated;
