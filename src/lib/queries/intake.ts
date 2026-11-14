/**
 * Query hooks for AI intake runs (step 2).
 *
 * Purpose: The latest run is polled while it works; its scope items exist only once it finishes,
 * so the scope list is refetched exactly then.
 * How it fits: Used by `screens/AIProjectIntake.tsx` and `screens/ScopeReview.tsx`;
 * layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { apiFetch, apiFetchOrNull, isApiError } from '../api'
import type { IntakeRun } from '../types'
import { queryKeys } from './keys'
import { intakeRunPath } from './paths'
import { isRunning, pollWhileRunning } from './polling'

/**
 * A run's scope items exist only once it has finished, so the scope list (and the "What the AI
 * found" counts built from it) must be refetched then, not when the run is queued.
 */
function refreshAfterIntakeRun(queryClient: QueryClient, projectId: string) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.scopeItems(projectId) })
}

/**
 * Latest AI intake run for a project (`null` when none yet); polls while it is queued or running
 * and refreshes the scope items when a poll sees the run finish.
 */
export function useLatestIntakeRun(projectId: string) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: queryKeys.latestIntakeRun(projectId),
    queryFn: async ({ signal }) => {
      const previous = queryClient.getQueryData<IntakeRun | null>(queryKeys.latestIntakeRun(projectId))
      const run = await apiFetchOrNull<IntakeRun>(intakeRunPath(projectId, '/latest'), { signal })
      if (run === null) return null
      if (isRunning(previous?.status) && !isRunning(run.status)) refreshAfterIntakeRun(queryClient, projectId)
      return run
    },
    enabled: Boolean(projectId),
    refetchInterval: (query) => pollWhileRunning(query.state.data?.status),
  })
}

/** Starts an AI intake run on the given notes and shows it as the latest run. */
export function useStartIntakeRun(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (text: string) => apiFetch<IntakeRun>(intakeRunPath(projectId), { method: 'POST', json: { text } }),
    onSuccess: (run) => {
      queryClient.setQueryData(queryKeys.latestIntakeRun(projectId), run)
      // Usually queued: the poll refreshes the scope items once it finishes. A run that came back
      // already finished has its items now.
      if (!isRunning(run.status)) refreshAfterIntakeRun(queryClient, projectId)
    },
    onError: (error) => {
      // Someone else already started one: show it.
      if (isApiError(error) && error.status === 409) void queryClient.invalidateQueries({ queryKey: queryKeys.latestIntakeRun(projectId) })
    },
  })
}
