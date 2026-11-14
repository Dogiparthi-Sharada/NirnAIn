/**
 * WBS Builder data rules (step 4): estimate provenance, the Continue gate and the confirm hook.
 *
 * Business rule ("never invent"): an AI effort estimate is only a suggestion until the PM confirms
 * it; the optimizer may use a work package's duration only when it is confirmed (an estimate the PM
 * entered counts as confirmed). Continue to Constraints is never disabled — a blocked click lists
 * what still needs the PM (see `wbsBlockers`). Server contract [B]: `GET …/wbs` nodes carry
 * `estimateConfirmed` and `estimateSource`, and `POST …/wbs/confirm-estimates {nodeIds?}` confirms
 * some or all AI estimates. Used by `screens/WBSBuilder.tsx` and `components/WbsTable.tsx`.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'
import { queryKeys } from './queries'
import type { WbsNode } from './types'

/** Durations are stored in working days; the builder shows weeks. */
export const DAYS_PER_WEEK = 5

/** A WBS node with the estimate fields of contract [B]. */
export type WbsTreeNode = WbsNode & {
  /** True when the solver may use the duration: an AI estimate the PM confirmed, or one the PM entered. */
  estimateConfirmed: boolean
  estimateSource: 'ai' | 'yours' | null
}

/** Where a work package's duration stands: what its chip says and whether the solver may use it. */
export type EstimateState = 'ai' | 'confirmed' | 'yours' | 'missing'

/** "3w", "2.5w"; null when there is no duration. */
export function formatWeeks(days: number | null): string | null {
  if (days == null) return null
  const weeks = days / DAYS_PER_WEEK
  return `${Number.isInteger(weeks) ? weeks : weeks.toFixed(1)}w`
}

/** "3 weeks", "1 week", "2.5 weeks" for banners and labels. */
export function weeksLabel(days: number): string {
  const weeks = Math.round((days / DAYS_PER_WEEK) * 10) / 10
  return `${weeks} week${weeks === 1 ? '' : 's'}`
}

/** The root shows as "1.0" like the design; other nodes use the server-derived codes. */
export function displayCode(node: WbsNode): string {
  return node.level === 1 ? '1.0' : node.code
}

/** The estimate state of a work package, from the server's confirmation fields. */
export function estimateState(node: WbsTreeNode): EstimateState {
  if (node.durationDays == null) return 'missing'
  if (!node.estimateConfirmed) return 'ai'
  return node.estimateSource === 'ai' ? 'confirmed' : 'yours'
}

/** Work packages whose duration the solver may not use yet, in tree order. */
export function wbsBlockers(nodes: WbsTreeNode[]): WbsTreeNode[] {
  return nodes.filter((node) => {
    const state = estimateState(node)
    return node.level === 3 && (state === 'ai' || state === 'missing')
  })
}

/**
 * The `position` for `POST /wbs/nodes/{id}/move` that puts `source` where `target` is among their
 * shared siblings; null when the move is not allowed (other parent, the root) or changes nothing.
 * The server renumbers the codes.
 */
export function movePosition(nodes: WbsNode[], source: WbsNode, target: WbsNode): number | null {
  if (source.id === target.id || source.level === 1 || source.parentId !== target.parentId) return null
  const siblings = nodes.filter((node) => node.parentId === source.parentId)
  const from = siblings.findIndex((node) => node.id === source.id)
  const to = siblings.findIndex((node) => node.id === target.id)
  const position = from < to ? to - 1 : to
  return position === from ? null : position
}

/** Total weeks of all work packages with a duration. */
export function totalWeeks(nodes: WbsTreeNode[]): number {
  const days = nodes.filter((node) => node.level === 3).reduce((sum, node) => sum + (node.durationDays ?? 0), 0)
  return Math.round((days / DAYS_PER_WEEK) * 10) / 10
}

/**
 * Confirms AI estimates (`nodeIds` omitted = all of them) and stores the returned tree, so the
 * chips and the gate update at once.
 */
export function useConfirmEstimates(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (nodeIds?: string[]) =>
      apiFetch<WbsTreeNode[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/wbs/confirm-estimates`, {
        method: 'POST',
        json: nodeIds ? { nodeIds } : {},
      }),
    onSuccess: (tree) => queryClient.setQueryData(queryKeys.wbs(projectId), tree),
  })
}
