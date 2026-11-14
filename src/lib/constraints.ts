/**
 * Step 5, Constraints: the rules behind the screen (`screens/Constraints.tsx` and
 * `screens/constraints/*`), kept out of the components so they stay about layout.
 *
 * - Saving: the constraint set is replaced as a whole by `PUT …/constraints`, so
 *   `useConstraintEditor` merges each small change into the cached set first (an optimistic
 *   update), letting quick clicks build on each other instead of overwriting one another.
 * - The question that changes the plan: the deadline answer IS the deadline's Must hold / Can flex
 *   setting. (Skills are not part of planning, so there is no specialist question.)
 * - The Run Optimization gate: `gateBlockers` merges what this screen knows (the answer, team, WBS)
 *   with the server's readiness check (`GET …/optimization/inputs`), so the button is never
 *   disabled but a blocked click can name every missing input.
 * - The plan lock: once a plan version is applied the inputs are read-only and the server answers
 *   409 "Plan vN is in force; record a change on step 7"; changes go through step 7.
 */
import { useCallback } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch, errorMessage, isApiError } from './api'
import type { PolicyKey, PolicyMode } from './constraintPolicy'
import { budgetProblem } from './createForm'
import { formatDate } from './format'
import { inputKeys, type ConstraintInputs, type DeadlineAnswerValue, type PlanVersion, type ProjectTeamMember } from './inputs'
import { queryKeys } from './queries'
import { toast } from './toast'

/** A partial edit of the constraint set; the editor fills in the rest from the saved set. */
export type ConstraintChanges = Partial<ConstraintInputs>

/* ───────────────────────────── Plan lock ───────────────────────────── */

/** The plan version in force (the latest applied one), or null while inputs are still editable. */
export function planLockOf(versions: PlanVersion[] | undefined): number | null {
  if (!versions || versions.length === 0) return null
  return Math.max(...versions.map((planVersion) => planVersion.version))
}

/** What a click on a locked input explains (same wording as the approved design). */
export function lockedMessage(version: number): string {
  return `Plan v${version} is applied. Use step 7, Changes & Versions, to change its inputs.`
}

/** True for the server's 409 "Plan vN is in force; record a change on step 7". */
export function isPlanLockError(error: unknown): boolean {
  if (!isApiError(error) || error.status !== 409) return false
  return typeof error.extensions.version === 'number' || /is in force/i.test(error.detail)
}

/* ───────────────────────────── Saving ───────────────────────────── */

type QueryClient = ReturnType<typeof useQueryClient>

/**
 * A solver input changed: the step 6 confirmation sheet, the team's booked hours (they depend on
 * the plan window) and the coverage views must refresh. A lock error also refreshes the plan
 * versions so the screen switches to its read-only state.
 */
export function refreshPlanningInputs(queryClient: QueryClient, projectId: string, error?: unknown): void {
  void queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'optimization'] })
  void queryClient.invalidateQueries({ queryKey: inputKeys.team(projectId) })
  void queryClient.invalidateQueries({ queryKey: queryKeys.capacity(projectId) })
  if (isPlanLockError(error)) void queryClient.invalidateQueries({ queryKey: inputKeys.planVersions(projectId) })
}

/** `refreshPlanningInputs` for this project, for mutations made outside this module. */
export function useInputsRefresh(projectId: string): (error?: unknown) => void {
  const queryClient = useQueryClient()
  return useCallback((error?: unknown) => refreshPlanningInputs(queryClient, projectId, error), [queryClient, projectId])
}

/** Reports a failed save in a toast; a plan-lock 409 also switches the screen to read-only. */
export function useSaveFailure(projectId: string): (error: unknown) => void {
  const refresh = useInputsRefresh(projectId)
  return useCallback(
    (error: unknown) => {
      toast(errorMessage(error))
      refresh(error)
    },
    [refresh],
  )
}

/**
 * Saves partial changes to the project's constraint set (see the module comment). Saves run one at
 * a time (one mutation scope per project), so the last click is also the last write; each save
 * sends the cached set as it is when the save starts, which already holds the earlier changes.
 */
export function useConstraintEditor(projectId: string) {
  const queryClient = useQueryClient()
  const cacheKey = queryKeys.constraints(projectId)
  const mutationKey = ['save-constraints', projectId]

  /** `base` with the changes of every save still waiting applied on top, oldest first. */
  function withWaitingChanges(base: ConstraintInputs, skip?: ConstraintChanges): ConstraintInputs {
    return queryClient
      .getMutationCache()
      .findAll({ mutationKey, status: 'pending' })
      .map((mutation) => mutation.state.variables as ConstraintChanges)
      .filter((changes) => changes !== skip)
      .reduce<ConstraintInputs>((current, changes) => ({ ...current, ...changes }), base)
  }

  return useMutation({
    mutationKey,
    scope: { id: `save-constraints:${projectId}` },
    onMutate: async (changes: ConstraintChanges) => {
      await queryClient.cancelQueries({ queryKey: cacheKey })
      const previous = queryClient.getQueryData<ConstraintInputs>(cacheKey)
      if (previous) queryClient.setQueryData<ConstraintInputs>(cacheKey, { ...previous, ...changes })
      return { previous }
    },
    mutationFn: (changes: ConstraintChanges) =>
      apiFetch<ConstraintInputs>(`/api/v1/projects/${encodeURIComponent(projectId)}/constraints`, {
        method: 'PUT',
        json: { ...queryClient.getQueryData<ConstraintInputs>(cacheKey), ...changes },
      }),
    onSuccess: async (saved) => {
      // A background refetch that started before this write must not land on top of it.
      await queryClient.cancelQueries({ queryKey: cacheKey })
      queryClient.setQueryData(cacheKey, withWaitingChanges(saved))
      refreshPlanningInputs(queryClient, projectId)
    },
    onError: async (error, changes, context) => {
      await queryClient.cancelQueries({ queryKey: cacheKey })
      if (context?.previous) queryClient.setQueryData(cacheKey, withWaitingChanges(context.previous, changes))
      if (queryClient.isMutating({ mutationKey }) <= 1) void queryClient.invalidateQueries({ queryKey: cacheKey })
      refreshPlanningInputs(queryClient, projectId, error)
    },
  })
}

/**
 * The deadline answer and the deadline's Must hold / Can flex are one setting: "Can move" means
 * Can flex; "Hard" and "Don't know" (the safer assumption) mean Must hold.
 */
export function deadlineChanges(answer: DeadlineAnswerValue): ConstraintChanges {
  return { deadlineAnswer: answer, deadlineMode: answer === 'soft' ? 'soft' : 'hard' }
}

/**
 * Must hold (hard) = the solver never breaks the limit; Can flex (soft) = it may bend, with a
 * penalty, if that is the only way.
 */
export function policyChanges(key: PolicyKey, mode: PolicyMode): ConstraintChanges {
  if (key === 'deadline') return deadlineChanges(mode)
  if (key === 'budget') return { budgetMode: mode }
  return { capacityMode: mode }
}

/* ───────────────────────────── Questions ───────────────────────────── */

/** "Is Sep 30, 2025 a hard deadline?" (the project's target end date when step 5 has no date). */
export function deadlineQuestion(mustFinishBy: string | null): string {
  return mustFinishBy ? `Is ${formatDate(mustFinishBy)} a hard deadline?` : 'Is the target end date a hard deadline?'
}

/** Low-impact settings we use without asking (all editable on the cards below). */
export function checkedNotAsked(constraints: ConstraintInputs): string {
  const milestoneModes = new Set(constraints.milestones.map((milestone) => milestone.mode))
  const milestones =
    milestoneModes.size === 0 ? 'no phase milestones' : `phase milestones ${milestoneModes.size > 1 ? 'hard and soft' : [...milestoneModes][0]}`
  const capsSet = constraints.laborCapCents != null || constraints.infraCapCents != null
  return [
    `working days ${constraints.workingDaysPerWeek} per week`,
    `contingency ${constraints.contingencyPct}%`,
    milestones,
    `labor and infrastructure caps ${capsSet ? 'as entered' : 'not set'}`,
  ].join(' \u00B7 ')
}

/* ───────────────────────────── Run Optimization gate ───────────────────────────── */

/** One missing input that keeps the optimizer from running, as the banner and status line show it. */
export interface GateBlocker {
  /** The server's `missing` key where there is one: estimates, deadline, team… */
  key: string
  /** Words for the status line: "To continue: answer the deadline question." */
  hint: string
  /** The banner line. */
  label: string
  /** Where its link goes: an element on this page, step 4, or nowhere. */
  target: { selector: string } | { step: 'wbs' } | null
}

/** What the gate looks at; `undefined` means that data has not loaded (yet). */
export interface GateFacts {
  constraints: ConstraintInputs | undefined
  team: ProjectTeamMember[] | undefined
  workPackages: number | undefined
  openEstimates: number | undefined
  /** Keys and messages from `GET …/optimization/inputs` (`missing` / `blocking`). */
  serverMissing: string[]
  serverBlocking: string[]
}

/** Element ids the gate's links move focus to. */
export const DEADLINE_QUESTION_ID = 'q-deadline'
export const ADD_MEMBER_ID = 'btn-add-member'

/**
 * What still keeps the optimizer from running, in the design's order: confirmed effort, the
 * deadline question (once there is a date), and at least one person on the team.
 */
export function gateBlockers(facts: GateFacts): GateBlocker[] {
  const blockers: GateBlocker[] = []
  const serverSays = (key: string) => facts.serverMissing.includes(key)
  const toWbs = { step: 'wbs' } as const

  if (facts.workPackages === 0 || (facts.workPackages === undefined && serverSays('workPackages'))) {
    blockers.push({ key: 'workPackages', hint: 'add work packages', label: 'Add work packages with durations on step 4, WBS Builder', target: toWbs })
  } else if ((facts.openEstimates ?? 0) > 0 || (facts.openEstimates === undefined && serverSays('estimates'))) {
    blockers.push({ key: 'estimates', hint: 'confirm the estimates', label: 'Confirm the effort estimates on step 4, WBS Builder', target: toWbs })
  }

  const constraints = facts.constraints
  if (!constraints) {
    blockers.push({ key: 'constraints', hint: 'wait for the settings to load', label: 'The constraint settings have not loaded yet', target: null })
  } else {
    // Without a date here the server still plans to the project's target end date, so it may ask too.
    if (constraints.deadlineAnswer == null && (constraints.mustFinishBy || serverSays('deadline'))) {
      const question = deadlineQuestion(constraints.mustFinishBy)
      blockers.push({ key: 'deadline', hint: 'answer the deadline question', label: `Answer \u201C${question}\u201D`, target: { selector: `#${DEADLINE_QUESTION_ID} .opt` } })
    }
  }

  if (!facts.team) {
    blockers.push({ key: 'team', hint: 'wait for the team to load', label: 'The team has not loaded yet', target: null })
  } else if (facts.team.length === 0) {
    blockers.push({ key: 'team', hint: 'add at least one team member', label: 'Add at least one team member', target: { selector: `#${ADD_MEMBER_ID}` } })
  }

  // Anything else the server still reports (worded by the server), e.g. a check this screen doesn't know.
  const known = new Set(['workPackages', 'estimates', 'deadline', 'team'])
  facts.serverMissing.forEach((key, position) => {
    if (known.has(key)) return
    const message = facts.serverBlocking[position] ?? key
    blockers.push({ key, hint: message.charAt(0).toLowerCase() + message.slice(1), label: message, target: null })
  })
  return blockers
}

/* ───────────────────────────── Number fields ───────────────────────────── */

/** "$ 375,000" (whole dollars) or "$ 175.50"; empty for no value. */
export function dollarsText(cents: number | null | undefined): string {
  if (cents == null) return ''
  const dollars = cents / 100
  return `$ ${dollars.toLocaleString('en-US', { maximumFractionDigits: 2 })}`
}

/** Cents from "$ 375,000", "375000" or "175.50"; null for empty; NaN when it isn't an amount. */
export function parseDollars(text: string): number | null {
  const cleaned = text.replace(/[$,\s]/g, '')
  if (cleaned === '') return null
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return Number.NaN
  return Math.round(Number(cleaned) * 100)
}

/**
 * Why a budget amount typed on step 5 can't be saved, or null when it can. Same rules as step 1
 * (`budgetProblem`): dollars and cents only, more than $0, and at most $100B so a typo with extra
 * zeros is caught. A blank field is fine: it clears the limit.
 */
export function moneyLimitProblem(text: string): string | null {
  return text.replace(/[$,\s]/g, '') === '' ? null : budgetProblem(text)
}

/** A percentage from 0 to 100 from "10" or "10 %"; NaN otherwise. */
export function parsePercent(text: string): number {
  const cleaned = text.replace(/[%\s]/g, '')
  if (!/^\d+(\.\d+)?$/.test(cleaned)) return Number.NaN
  const value = Number(cleaned)
  return value <= 100 ? value : Number.NaN
}

/** Hours with at most one decimal: 36, 12.5. */
export function hoursText(hours: number): string {
  return Number.isInteger(hours) ? String(hours) : hours.toFixed(1)
}
