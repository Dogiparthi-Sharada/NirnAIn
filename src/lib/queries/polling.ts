/**
 * Polling rule for server jobs: poll every two seconds while queued or running.
 *
 * Purpose: Optimization runs, intake runs and AI jobs run in the background; screens show progress
 * by polling and must stop as soon as the job is done.
 * How it fits: Used by the run and job queries in `lib/queries/` and `lib/changes.ts` and by
 * screens that show a job's progress; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

/** How often a queued or running server job is polled. */
export const POLL_INTERVAL_MS = 2000

/** True while a server job (an optimization run, intake run or AI job) is still queued or running. */
export function isRunning(status: string | null | undefined): boolean {
  return status === 'queued' || status === 'running'
}

/** A query's `refetchInterval` for a job: poll while it is queued or running, then stop. */
export function pollWhileRunning(status: string | null | undefined): number | false {
  return isRunning(status) ? POLL_INTERVAL_MS : false
}
