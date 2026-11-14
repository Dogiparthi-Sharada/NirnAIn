/**
 * WBS Builder data rules (step 4): estimate provenance, the Continue gate and the confirm hook.
 *
 * Business rule ("never invent"): an AI effort estimate is only a suggestion until the PM confirms
 * it; the optimizer may use a work package's duration only when it is confirmed (an estimate the PM
 * entered counts as confirmed). Continue to Constraints is never disabled — a blocked click lists
 * what still needs the PM (see `wbsBlockers`). Server contract [B]: `GET …/wbs` nodes carry
 * `estimateConfirmed` and `estimateSource`, and `POST …/wbs/confirm-estimates {nodeIds?}` confirms
 * some or all AI estimates. Used by `screens/WBSBuilder.tsx` and `components/WbsTable.tsx`.
 *
 * Skills (D6): a work package names the skills it needs, picked from the project's skill list
 * (step 5), with `PATCH /wbs/nodes/{nodeId} {skills}`; the optimizer links Required skills to the
 * packages that carry them. Servers without that field answer 422, and the builder then hides the
 * picker and says why (see `useWbsSkills`).
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch, errorMessage, isApiError } from './api'
import { useProjectSkills, type ProjectSkill } from './inputs'
import { queryKeys } from './queries'
import type { WbsNode, WbsSkill } from './types'

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

/** The longest duration the server accepts: 3,650 working days, i.e. 730 weeks. */
export const MAX_DURATION_DAYS = 3650

/** Weeks typed for a work package: the working days to save, or why they can't be saved. */
export type WeeksInput = { days: number | null; rounded: boolean; problem: null } | { days: null; rounded: false; problem: string }

/**
 * Reads the weeks typed in a work package's Duration field. Blank = no estimate yet. Negative or
 * non-numeric text (including "2,5": we don't guess which separator was meant) is refused with a
 * reason. Durations are whole working days on the wire, so a fraction is rounded to the nearest
 * day and `rounded` says so, letting the editor show it ("1.5 w = 8 days").
 */
export function parseWeeks(text: string): WeeksInput {
  const cleaned = text.trim()
  if (cleaned === '') return { days: null, rounded: false, problem: null }
  if (cleaned.startsWith('-')) return { days: null, rounded: false, problem: 'Weeks can\u2019t be negative.' }
  if (!/^(\d+(\.\d*)?|\.\d+)$/.test(cleaned)) {
    return { days: null, rounded: false, problem: 'Enter the weeks as a number, for example 2 or 1.5.' }
  }
  const exactDays = Number(cleaned) * DAYS_PER_WEEK
  const days = Math.round(exactDays)
  if (days > MAX_DURATION_DAYS) {
    return { days: null, rounded: false, problem: `A work package can last at most ${MAX_DURATION_DAYS / DAYS_PER_WEEK} weeks. Check for extra digits.` }
  }
  return { days, rounded: Math.abs(days - exactDays) > 1e-9, problem: null }
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

/** True for the 422 a server without the `skills` field gives ("Extra inputs are not permitted"). */
export function isSkillsFieldUnknown(error: unknown): boolean {
  return isApiError(error) && error.status === 422 && /extra inputs/i.test(error.fieldErrors.skills ?? '')
}

/**
 * Saves the skills a work package needs and stores the returned node. The step 6 inputs sheet and
 * the skills coverage depend on them, so both refresh. `unsupported` turns true (for this visit)
 * once the server refuses the field, so the builder can stop offering the picker.
 */
function useSaveWbsSkills(projectId: string) {
  const queryClient = useQueryClient()
  const [unsupported, setUnsupported] = useState(false)
  const mutation = useMutation({
    mutationFn: ({ nodeId, skills }: { nodeId: string; skills: string[] }) =>
      apiFetch<WbsTreeNode>(`/api/v1/wbs/nodes/${encodeURIComponent(nodeId)}`, { method: 'PATCH', json: { skills } }),
    onSuccess: (saved) => {
      queryClient.setQueryData<WbsTreeNode[]>(queryKeys.wbs(projectId), (previous) => previous?.map((node) => (node.id === saved.id ? saved : node)))
      void queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'optimization'] })
      void queryClient.invalidateQueries({ queryKey: queryKeys.skillsCoverage(projectId) })
    },
    onError: (error) => {
      if (isSkillsFieldUnknown(error)) setUnsupported(true)
    },
  })
  return { mutation, unsupported }
}

/** What an edit row needs to know about the project's skill list and the server. */
export interface SkillOptions {
  projectId: string
  /** The project's skill list; undefined while loading or after an error. */
  skills: ProjectSkill[] | undefined
  loading: boolean
  /** Why the list couldn't load, else null. */
  error: string | null
  retry: () => void
  /** The server refused the `skills` field (D6 not deployed yet): the picker is hidden. */
  unsupported: boolean
}

/** Names compare without case, as the server does. */
export function skillNameKey(name: string): string {
  return name.toLowerCase()
}

/** The names a package can carry: the project's list, then the package's own skills not on it. */
export function skillChoices(projectSkills: ProjectSkill[] | undefined, current: WbsSkill[]): string[] {
  const listed = (projectSkills ?? []).map((skill) => skill.name)
  const listedKeys = new Set(listed.map(skillNameKey))
  return [...listed, ...current.map((skill) => skill.name).filter((name) => !listedKeys.has(skillNameKey(name)))]
}

/** The picker's state: the picked names (case-insensitive) and the names to save, in list order. */
export function useSkillPicks(current: WbsSkill[]) {
  const [picked, setPicked] = useState<Set<string>>(() => new Set(current.map((skill) => skillNameKey(skill.name))))

  function toggle(name: string) {
    setPicked((previous) => {
      const next = new Set(previous)
      if (next.has(skillNameKey(name))) next.delete(skillNameKey(name))
      else next.add(skillNameKey(name))
      return next
    })
  }

  /** The names to save, or null when the PM changed nothing. */
  function changedNames(choices: string[]): string[] | null {
    const names = choices.filter((name) => picked.has(skillNameKey(name)))
    const before = current.map((skill) => skillNameKey(skill.name)).sort().join('\n')
    return names.map(skillNameKey).sort().join('\n') === before ? null : names
  }

  return { toggle, changedNames, isPicked: (name: string) => picked.has(skillNameKey(name)) }
}

/**
 * The builder's work-package skills: the options for the chips and pickers (the project's skill
 * list and whether the server takes skills), the save, and its error unless it is the refusal.
 */
export function useWbsSkills(projectId: string) {
  const skillList = useProjectSkills(projectId)
  const { mutation, unsupported } = useSaveWbsSkills(projectId)
  const options: SkillOptions = {
    projectId,
    skills: skillList.data,
    loading: skillList.isPending,
    error: skillList.isError ? errorMessage(skillList.error) : null,
    retry: () => void skillList.refetch(),
    unsupported,
  }
  return { options, save: mutation.mutate, error: unsupported ? null : mutation.error }
}
