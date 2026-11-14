/**
 * Query hooks for the project's constraint set and team capacity (step 5).
 *
 * Purpose: Loads and saves the constraint set; capacity depends on the plan window, so a save
 * refreshes it.
 * How it fits: Used by `screens/Constraints.tsx`, `screens/OptimizationResults.tsx` and the
 * change-edit panes; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api'
import type { ConstraintSet, TeamMember } from '../types'
import { queryKeys } from './keys'
import { projectPath } from './paths'

/** Fetches the project's constraint set (budget, deadline, capacity and skill rules). */
export function useConstraints(projectId: string) {
  return useQuery({
    queryKey: queryKeys.constraints(projectId),
    queryFn: ({ signal }) => apiFetch<ConstraintSet>(projectPath(projectId, '/constraints'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Replaces the constraint set, caches the saved one and refreshes capacity (it depends on the plan window). */
export function useSaveConstraints(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (constraints: ConstraintSet) =>
      apiFetch<ConstraintSet>(projectPath(projectId, '/constraints'), { method: 'PUT', json: constraints }),
    onSuccess: (saved) => {
      queryClient.setQueryData(queryKeys.constraints(projectId), saved)
      void queryClient.invalidateQueries({ queryKey: queryKeys.capacity(projectId) })
    },
  })
}

/** Fetches the capacity of the people available to the project. */
export function useCapacity(projectId: string) {
  return useQuery({
    queryKey: queryKeys.capacity(projectId),
    queryFn: ({ signal }) => apiFetch<TeamMember[]>(projectPath(projectId, '/capacity'), { signal }),
    enabled: Boolean(projectId),
  })
}
