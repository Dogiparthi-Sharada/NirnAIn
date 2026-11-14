/**
 * Scope Review's own list of scope items, removed ones included.
 *
 * Purpose: Removed items must stay listed with Undo after a reload (QA S-15), while the shared
 * `useScopeItems` list (AI Intake, WBS Builder) keeps leaving them out.
 * How it fits: Used by `screens/ScopeReview.tsx`; its key sits under `queryKeys.scopeItems`, so
 * every refresh of the shared list (a new intake run) refreshes this one too; layer: screen.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { apiFetch } from '@/lib/api'
import { queryKeys } from '@/lib/queries'
import type { ScopeItem } from '@/lib/types'

/** Query key of the list with removed items (a child of the shared scope-items key). */
export function scopeListKey(projectId: string) {
  return [...queryKeys.scopeItems(projectId), 'with-removed'] as const
}

/** Every scope item of the project, removed ones included. */
export function useScopeList(projectId: string) {
  return useQuery({
    queryKey: scopeListKey(projectId),
    queryFn: ({ signal }) =>
      apiFetch<ScopeItem[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/scope-items?includeRemoved=true`, { signal }),
    enabled: Boolean(projectId),
  })
}

/** Puts a saved item (new or changed) into the list with removed items. */
export function useScopeListSync(projectId: string): (item: ScopeItem) => void {
  const queryClient = useQueryClient()
  return useCallback(
    (item: ScopeItem) =>
      queryClient.setQueryData<ScopeItem[]>(scopeListKey(projectId), (previous) => {
        if (!previous) return previous
        return previous.some((known) => known.id === item.id)
          ? previous.map((known) => (known.id === item.id ? item : known))
          : [...previous, item]
      }),
    [queryClient, projectId],
  )
}
