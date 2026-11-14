/**
 * Query hooks for dependencies between work packages (step 5).
 *
 * Purpose: Loads, adds, retypes and deletes dependencies, updating the cached list in place.
 * How it fits: Used by `screens/constraints/DependenciesCard.tsx`; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api'
import type { Dependency, DependencyCreate, DependencyType } from '../types'
import { queryKeys } from './keys'
import { projectPath } from './paths'

/** Fetches the dependencies between the project's work packages. */
export function useDependencies(projectId: string) {
  return useQuery({
    queryKey: queryKeys.dependencies(projectId),
    queryFn: ({ signal }) => apiFetch<Dependency[]>(projectPath(projectId, '/dependencies'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Adds a dependency and appends it to the cached list. */
export function useCreateDependency(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (dependency: DependencyCreate) =>
      apiFetch<Dependency>(projectPath(projectId, '/dependencies'), { method: 'POST', json: dependency }),
    onSuccess: (dependency) => {
      queryClient.setQueryData<Dependency[]>(queryKeys.dependencies(projectId), (previous) =>
        previous ? [...previous, dependency] : [dependency],
      )
    },
  })
}

/** Changes a dependency's type and swaps it into the cached list. */
export function useUpdateDependency(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ dependencyId, type }: { dependencyId: string; type: DependencyType }) =>
      apiFetch<Dependency>(`/api/v1/dependencies/${encodeURIComponent(dependencyId)}`, { method: 'PATCH', json: { type } }),
    onSuccess: (dependency) => {
      queryClient.setQueryData<Dependency[]>(queryKeys.dependencies(projectId), (previous) =>
        previous?.map((existing) => (existing.id === dependency.id ? dependency : existing)),
      )
    },
  })
}

/** Deletes a dependency and drops it from the cached list. */
export function useDeleteDependency(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (dependencyId: string) =>
      apiFetch<void>(`/api/v1/dependencies/${encodeURIComponent(dependencyId)}`, { method: 'DELETE' }),
    onSuccess: (_void, dependencyId) => {
      queryClient.setQueryData<Dependency[]>(queryKeys.dependencies(projectId), (previous) =>
        previous?.filter((existing) => existing.id !== dependencyId),
      )
    },
  })
}
