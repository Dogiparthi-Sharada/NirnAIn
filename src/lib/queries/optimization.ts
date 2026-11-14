/**
 * Query hooks for the latest optimization run and applying a run as the plan in force.
 *
 * Purpose: The latest run is polled while it solves; applying it changes the project, its WBS
 * dates and every org list, so all of those refresh.
 * How it fits: Used by `screens/OptimizationResults.tsx`, `screens/optimization/*` and the
 * dashboard; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, apiFetchOrNull } from '../api'
import type { OptimizationRun } from '../types'
import { queryKeys } from './keys'
import { runPath } from './paths'
import { pollWhileRunning } from './polling'

/** Latest optimization run for a project (`null` when none yet); polls while it is queued or running. */
export function useLatestRun(projectId: string) {
  return useQuery({
    queryKey: queryKeys.latestRun(projectId),
    queryFn: ({ signal }) => apiFetchOrNull<OptimizationRun>(runPath(projectId, '/latest'), { signal }),
    refetchInterval: (query) => pollWhileRunning(query.state.data?.status),
  })
}

/**
 * Applies a run as the project's plan in force. The project, its WBS dates and every org list or
 * dashboard showing it change, so all of those are refreshed.
 */
export function useApplyRun(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      apiFetch<OptimizationRun>(runPath(projectId, `/${encodeURIComponent(runId)}/apply`), { method: 'POST', json: {} }),
    onSuccess: (run) => {
      queryClient.setQueryData(queryKeys.latestRun(projectId), run)
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.wbs(projectId) })
      void queryClient.invalidateQueries({ queryKey: ['orgs'] })
    },
  })
}
