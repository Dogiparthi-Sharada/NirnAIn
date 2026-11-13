-- =====================================================================
-- AI intake runs: at most one queued/running run per project, so
-- double-clicks or two teammates can't start parallel analyses (mirrors
-- optimization_runs_one_active_per_project). The API maps the unique
-- violation (23505) to 409.
-- =====================================================================

-- Existing duplicates would block the index: keep the newest active run per
-- project and mark older ones failed (they were orphaned anyway).
update public.intake_runs r
set status = 'failed',
    error = 'Superseded by a newer analysis.',
    finished_at = now()
where r.status in ('queued', 'running')
  and exists (
    select 1 from public.intake_runs n
    where n.project_id = r.project_id
      and n.status in ('queued', 'running')
      and (n.created_at, n.id) > (r.created_at, r.id)
  );

create unique index if not exists intake_runs_one_active_per_project
  on public.intake_runs (project_id)
  where status in ('queued', 'running');
