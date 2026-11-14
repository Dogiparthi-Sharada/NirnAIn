/**
 * Step 5, Constraints: the rules behind the screen (`screens/Constraints.tsx` and
 * `screens/constraints/*`), kept out of the components so they stay about layout.
 *
 * - Saving: the constraint set is replaced as a whole by `PUT …/constraints`, so
 *   `useConstraintEditor` merges each small change into the cached set first (an optimistic
 *   update), letting quick clicks build on each other instead of overwriting one another.
 * - The two questions that change the plan: the deadline answer IS the deadline's Must hold /
 *   Can flex setting, and the specialist answer decides whether outside contractors may be used.
 * - The Run Optimization gate: `gateBlockers` merges what this screen knows (answers, team, WBS)
 *   with the server's readiness check (`GET …/optimization/inputs`, which also sees work-package
 *   skill gaps), so the button is never disabled but a blocked click can name every missing input.
 * - The plan lock: once a plan version is applied the inputs are read-only and the server answers
 *   409 "Plan vN is in force; record a change on step 7"; changes go through step 7.
 */
import { useCallback } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch, errorMessage, isApiError } from './api'
import type { PolicyKey, PolicyMode } from './constraintPolicy'
import { formatDate } from './format'
import { inputKeys, type ConstraintInputs, type DeadlineAnswerValue, type PlanVersion, type ProjectTeamMember, type SpecialistAnswer } from './inputs'
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

/** Saves partial changes to the project's constraint set (see the module comment). */
export function useConstraintEditor(projectId: string) {
  const queryClient = useQueryClient()
  const cacheKey = queryKeys.constraints(projectId)
  const mutationKey = ['save-constraints', projectId]
  return useMutation({
    mutationKey,
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
    onSuccess: (saved) => {
      // A later save still in flight already carries this change; let its answer win.
      if (queryClient.isMutating({ mutationKey }) <= 1) queryClient.setQueryData(cacheKey, saved)
      refreshPlanningInputs(queryClient, projectId)
    },
    onError: (error, _changes, context) => {
      if (context?.previous) queryClient.setQueryData(cacheKey, context.previous)
      void queryClient.invalidateQueries({ queryKey: cacheKey })
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

/** Only a Yes lets the solver book outside contractors; "Don't know" is planned as No. */
export function specialistChanges(answer: SpecialistAnswer): ConstraintChanges {
  return { specialistAnswer: answer, allowContractors: answer === 'yes' }
}

/**
 * Must hold (hard) = the solver never breaks the limit; Can flex (soft) = it may bend, with a
 * penalty, if that is the only way.
 */
export function policyChanges(key: PolicyKey, mode: PolicyMode): ConstraintChanges {
  if (key === 'deadline') return deadlineChanges(mode)
  if (key === 'budget') return { budgetMode: mode }
  if (key === 'capacity') return { capacityMode: mode }
  return { skillMode: mode }
}

/* ───────────────────────────── Questions ───────────────────────────── */

/** "Can we bring in an outside SAP Integration specialist?" (generic when several skills are missing). */
export function specialistQuestion(gaps: string[]): string {
  return gaps.length === 1 ? `Can we bring in an outside ${gaps[0]} specialist?` : 'Can we bring in an outside specialist?'
}

/** "Is Sep 30, 2025 a hard deadline?" */
export function deadlineQuestion(mustFinishBy: string | null): string {
  return mustFinishBy ? `Is ${formatDate(mustFinishBy)} a hard deadline?` : 'Is there a hard deadline?'
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

export interface GateBlocker {
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
  /** Required skills on the project's list that nobody on the team has. */
  skillGaps: string[]
  /** Keys and messages from `GET …/optimization/inputs` (`missing` / `blocking`). */
  serverMissing: string[]
  serverBlocking: string[]
}

export const SPECIALIST_QUESTION_ID = 'q-specialist'
export const DEADLINE_QUESTION_ID = 'q-deadline'
export const ADD_MEMBER_ID = 'btn-add-member'

/**
 * What still keeps the optimizer from running, in the design's order: confirmed effort, the
 * specialist question (asked while a required skill is uncovered), the deadline question (once
 * there is a date), and at least one person on the team.
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
    if (constraints.specialistAnswer == null && (facts.skillGaps.length > 0 || serverSays('specialist'))) {
      const question = specialistQuestion(facts.skillGaps)
      blockers.push({ key: 'specialist', hint: 'answer the specialist question', label: `Answer \u201C${question}\u201D`, target: { selector: `#${SPECIALIST_QUESTION_ID} .opt` } })
    }
    if (constraints.deadlineAnswer == null && constraints.mustFinishBy) {
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
  const known = new Set(['workPackages', 'estimates', 'specialist', 'deadline', 'team'])
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
