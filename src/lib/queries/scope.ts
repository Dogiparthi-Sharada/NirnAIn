/**
 * Query hooks for the project's scope items (step 3, Scope Review).
 *
 * Purpose: Loads, adds and edits requirements, assumptions and risks, updating the cached list in
 * place so the review stays instant.
 * How it fits: Used by `screens/ScopeReview.tsx` and `screens/scope/*`; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api'
import type { ScopeItem, ScopeItemCreate, ScopeItemPatch } from '../types'
import { queryKeys } from './keys'
import { projectPath } from './paths'

/** Fetches the project's scope items (requirements, assumptions, risks…). */
export function useScopeItems(projectId: string) {
  return useQuery({
    queryKey: queryKeys.scopeItems(projectId),
    queryFn: ({ signal }) => apiFetch<ScopeItem[]>(projectPath(projectId, '/scope-items'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Adds a scope item and appends it to the cached list. */
export function useAddScopeItem(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (item: ScopeItemCreate) =>
      apiFetch<ScopeItem>(projectPath(projectId, '/scope-items'), { method: 'POST', json: item }),
    onSuccess: (item) => {
      queryClient.setQueryData<ScopeItem[]>(queryKeys.scopeItems(projectId), (previous) => (previous ? [...previous, item] : [item]))
    },
  })
}

/** Saves a change to one scope item and swaps it into the cached list. */
export function useUpdateScopeItem(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ itemId, patch }: { itemId: string; patch: ScopeItemPatch }) =>
      apiFetch<ScopeItem>(`/api/v1/scope-items/${encodeURIComponent(itemId)}`, { method: 'PATCH', json: patch }),
    onSuccess: (item) => {
      queryClient.setQueryData<ScopeItem[]>(queryKeys.scopeItems(projectId), (previous) =>
        previous ? previous.map((existing) => (existing.id === item.id ? item : existing)) : previous,
      )
    },
  })
}
