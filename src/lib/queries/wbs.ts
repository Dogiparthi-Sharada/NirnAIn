/**
 * Query hooks for the work breakdown structure (step 4) and its AI jobs.
 *
 * Purpose: WBS edits renumber codes, so each change refreshes the data built on them and re-runs
 * the free built-in checks, never the paid AI review.
 * How it fits: Used by `screens/WBSBuilder.tsx`, `components/WbsValidationCard.tsx` and step 5 and
 * 6 screens that read the WBS; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { apiFetch, apiFetchOrNull, isApiError } from '../api'
import type { AiJob, AiJobKind, WbsNode, WbsNodeCreate, WbsNodePatch } from '../types'
import { queryKeys } from './keys'
import { projectPath } from './paths'
import { pollWhileRunning } from './polling'

/**
 * WBS changes renumber codes, which dependency labels are built from, and they re-run the free
 * built-in WBS checks (never the AI review, which costs tokens).
 */
function invalidatePlanning(queryClient: QueryClient, projectId: string) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.dependencies(projectId) })
  void queryClient.invalidateQueries({ queryKey: queryKeys.wbsChecks(projectId) })
}

/** Fetches the project's work breakdown structure as a flat list of nodes. */
export function useWbs(projectId: string) {
  return useQuery({
    queryKey: queryKeys.wbs(projectId),
    queryFn: ({ signal }) => apiFetch<WbsNode[]>(projectPath(projectId, '/wbs'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Adds a WBS node, then refetches the tree (codes renumber) and the planning data built on it. */
export function useCreateWbsNode(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (node: WbsNodeCreate) => apiFetch<WbsNode>(projectPath(projectId, '/wbs/nodes'), { method: 'POST', json: node }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.wbs(projectId) })
      invalidatePlanning(queryClient, projectId)
    },
  })
}

/** Saves a change to one WBS node, swaps it into the cached tree and refreshes the planning data. */
export function useUpdateWbsNode(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ nodeId, patch }: { nodeId: string; patch: WbsNodePatch }) =>
      apiFetch<WbsNode>(`/api/v1/wbs/nodes/${encodeURIComponent(nodeId)}`, { method: 'PATCH', json: patch }),
    onSuccess: (node) => {
      queryClient.setQueryData<WbsNode[]>(queryKeys.wbs(projectId), (previous) =>
        previous?.map((existing) => (existing.id === node.id ? node : existing)),
      )
      invalidatePlanning(queryClient, projectId)
    },
  })
}

/** Moves a WBS node to a new position; the server answers with the renumbered tree. */
export function useMoveWbsNode(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ nodeId, position }: { nodeId: string; position: number }) =>
      apiFetch<WbsNode[]>(`/api/v1/wbs/nodes/${encodeURIComponent(nodeId)}/move`, { method: 'POST', json: { position } }),
    onSuccess: (tree) => {
      queryClient.setQueryData(queryKeys.wbs(projectId), tree)
      invalidatePlanning(queryClient, projectId)
    },
  })
}

/** Latest AI job of a kind (`null` when none yet); polls while it is queued or running. */
export function useLatestAiJob(projectId: string, kind: AiJobKind) {
  return useQuery({
    queryKey: queryKeys.latestAiJob(projectId, kind),
    queryFn: ({ signal }) => apiFetchOrNull<AiJob>(projectPath(projectId, `/ai-jobs/latest?kind=${kind}`), { signal }),
    enabled: Boolean(projectId),
    refetchInterval: (query) => pollWhileRunning(query.state.data?.status),
  })
}

/** Starts an AI job (generate or validate the WBS) and shows it as the latest job of its kind. */
export function useStartAiJob(projectId: string, kind: AiJobKind) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      apiFetch<AiJob>(projectPath(projectId, kind === 'wbs_generate' ? '/wbs/generate' : '/wbs/validate'), { method: 'POST', json: {} }),
    onSuccess: (job) => queryClient.setQueryData(queryKeys.latestAiJob(projectId, kind), job),
    onError: (error) => {
      // A job is already running (409): show it and the WBS it may have changed.
      if (isApiError(error) && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.latestAiJob(projectId, kind) })
        void queryClient.invalidateQueries({ queryKey: queryKeys.wbs(projectId) })
      }
    },
  })
}
