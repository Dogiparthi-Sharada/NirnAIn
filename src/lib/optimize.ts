/**
 * Step 6, Optimization Results: queries, mutations and derived values beyond the latest run.
 *
 * Purpose: The results screen needs the confirmation sheet (`GET …/optimization/inputs`), runs
 * started with a remedy, the specialist-rate confirmation, apply and export. Every key sits under
 * `['projects', id, 'optimization']`, which the input screens invalidate whenever a solver input
 * changes, so the sheet refreshes itself.
 * How it fits: Used by `screens/OptimizationResults.tsx` and `screens/optimization/*`; reuses the
 * shared run hooks from `lib/queries` and re-exports the wire types from `optimizeTypes.ts`;
 * layer: hook/lib.
 * Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDownload, apiFetch, isApiError, type ApiError } from './api'
import type { PolicyInputs } from './constraintPolicy'
import type { ConstraintAnswers, ExportFormat, OptimizationInputs, PlanRun, ProjectSkill, Remedy, RunRequest } from './optimizeTypes'
import { queryKeys } from './queries'
import { projectPath } from './queries/paths'

export type * from './optimizeTypes'

/** Rate the backend uses for an outside specialist when Constraints has none ($175/h). */
export const DEFAULT_SPECIALIST_RATE_CENTS = 17_500

/* ───────────────────────────── Queries ───────────────────────────── */

const optimizationKeys = {
  inputs: (projectId: string) => ['projects', projectId, 'optimization', 'inputs'] as const,
  skills: (projectId: string) => ['projects', projectId, 'skills'] as const,
  planVersions: (projectId: string) => ['projects', projectId, 'plan-versions'] as const,
}

/** The confirmation sheet for the next run. */
export function useOptimizationInputs(projectId: string) {
  return useQuery({
    queryKey: optimizationKeys.inputs(projectId),
    queryFn: ({ signal }) => apiFetch<OptimizationInputs>(projectPath(projectId, '/optimization/inputs'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** The project's skill list with holders, used to find required skills nobody on the team has. */
export function useProjectSkillList(projectId: string) {
  return useQuery({
    queryKey: optimizationKeys.skills(projectId),
    queryFn: ({ signal }) => apiFetch<ProjectSkill[]>(projectPath(projectId, '/skills'), { signal }),
    enabled: Boolean(projectId),
  })
}

/* ───────────────────────────── Mutations ───────────────────────────── */

/**
 * Starts a run (optionally with a remedy for an infeasible plan) and shows it straight away.
 * A 409 means a run is already going, or the inputs changed since the sheet was reviewed:
 * refetch both so the screen polls that run or shows the current inputs to review again.
 */
export function useStartOptimization(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (request: RunRequest) =>
      apiFetch<PlanRun>(projectPath(projectId, '/optimization/runs'), { method: 'POST', json: request }),
    onSuccess: (run) => {
      queryClient.setQueryData(queryKeys.latestRun(projectId), run)
      void queryClient.invalidateQueries({ queryKey: optimizationKeys.inputs(projectId) })
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.latestRun(projectId) })
        void queryClient.invalidateQueries({ queryKey: optimizationKeys.inputs(projectId) })
      }
    },
  })
}

/** After a plan is applied: plan versions (step 7, dashboard) and the sheet are out of date. */
export function useRefreshAfterApply(projectId: string): () => void {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: optimizationKeys.planVersions(projectId) })
    void queryClient.invalidateQueries({ queryKey: optimizationKeys.inputs(projectId) })
  }
}

/** After the constraint set is saved the sheet's specialist row changes ("estimated" goes away). */
export function useRefreshInputs(projectId: string): () => void {
  const queryClient = useQueryClient()
  return () => void queryClient.invalidateQueries({ queryKey: optimizationKeys.inputs(projectId) })
}

/** Downloads the plan of a run as a PDF report or a Microsoft Project XML file. */
export function exportRun(projectId: string, runId: string, format: ExportFormat): Promise<void> {
  const path = projectPath(projectId, `/optimization/runs/${encodeURIComponent(runId)}/export?format=${format}`)
  return apiDownload(path, format === 'pdf' ? 'plan.pdf' : 'plan.xml')
}

/* ───────────────────────────── Derived values ───────────────────────────── */

/** Required skills nobody on the team has. */
export function skillGapsOf(skills: ProjectSkill[] | undefined): string[] {
  return (skills ?? []).filter((skill) => skill.required && skill.holders.length === 0).map((skill) => skill.name)
}

/** The quoted budget sentence from the sheet's budget row, without its curly quotes. */
function budgetQuoteOf(inputs: OptimizationInputs | undefined): string | null {
  const budgetRow = inputs?.rows.find((row) => row.key === 'budget' && row.source === 'stated')
  return budgetRow ? budgetRow.from.replace(/^[\u201C"]|[\u201D"]$/g, '') : null
}

/** What `policyRisks()` needs, from the saved constraint set, the skill list and the sheet. */
export function policyInputsOf(
  constraints: ConstraintAnswers,
  skills: ProjectSkill[] | undefined,
  inputs: OptimizationInputs | undefined,
): PolicyInputs {
  return {
    budgetMode: constraints.budgetMode,
    capacityMode: constraints.capacityMode,
    skillMode: constraints.skillMode,
    deadlineAnswer: constraints.deadlineAnswer ?? null,
    budgetTotalCents: constraints.budgetTotalCents,
    mustFinishBy: constraints.mustFinishBy,
    skillGaps: skillGapsOf(skills),
    budgetQuote: budgetQuoteOf(inputs),
  }
}

/** True when the plan books an outside specialist, so its cost rests on a rate someone must confirm. */
export function usesSpecialist(run: PlanRun): boolean {
  return run.options?.remedy === 'specialist' || (run.result?.summary?.specialists.length ?? 0) > 0
}

/** A plan that finishes after the date it tries to keep, and the priced ways to keep that date. */
export interface DateSlip {
  completionDate: string
  targetDate: string
  /** Working days after `targetDate`. */
  slipDays: number
  /** Re-solved variants that finish earlier; accepting the later date is offered separately. */
  waysToKeep: Remedy[]
}

/**
 * When a plan slips past its target date, the PM is told and offered ways to keep it (nothing is
 * chosen for them). Null when the plan is on time, the server doesn't report the slip (older
 * servers), or the PM already chose to move the date for this run.
 */
export function dateSlipOf(run: PlanRun): DateSlip | null {
  const summary = run.result?.summary
  if (!summary?.targetDate || !summary.slipDays || summary.slipDays <= 0) return null
  if (run.options?.remedy === 'move_deadline') return null
  return {
    completionDate: summary.completionDate,
    targetDate: summary.targetDate,
    slipDays: summary.slipDays,
    waysToKeep: (run.result?.remedies ?? []).filter((remedy) => remedy.id !== 'move_deadline'),
  }
}

/** "3f9a1c…e21b": enough of the inputs hash to recognise it. */
export function shortHash(hash: string | null | undefined): string | null {
  return hash ? `${hash.slice(0, 6)}\u2026${hash.slice(-4)}` : null
}

/**
 * The inputs hash to compare with the sheet, to tell whether the latest run is out of date.
 * Only plain proposals qualify: an applied plan stays on screen as the plan in force (the sheet's
 * `appliedHash` tells whether inputs changed since), and a remedy or change-request run
 * deliberately differs from the saved inputs.
 */
export function comparableInputsHash(run: PlanRun | null): string | null {
  const options = run?.options
  if (!run || run.version != null || !options) return null
  if (options.remedy || options.changeRequestId || options.overrides) return null
  return options.inputsHash ?? null
}

/** Why Apply waits when the plan was solved from other inputs than the saved ones. */
export const OUTDATED_PLAN_REASON = 'Inputs changed since this plan was computed \u2014 run again'

/**
 * Whether a proposal (not yet applied) was solved from other inputs than the saved ones (`hash`
 * on the inputs sheet). Every run records the saved inputs' hash, remedy and re-plan runs too, and
 * the apply endpoint refuses such a plan (409); a run without a recorded hash can't prove it is
 * current either. False while the sheet's hash is unknown: the server still checks.
 */
export function solvedOnOtherInputs(run: PlanRun | null, savedHash: string | null | undefined): boolean {
  if (!run || run.version != null || !savedHash) return false
  return run.options?.inputsHash !== savedHash
}

/** A step-4/5 input the run endpoint reported missing (422 `missing` extension), with where to fix it. */
export interface MissingInputHint {
  key: string
  label: string
  step: 'wbs' | 'constraints'
  action: string
}

const MISSING_INPUT_HINTS: Record<string, Omit<MissingInputHint, 'key'>> = {
  workPackages: { label: 'Work packages with durations', step: 'wbs', action: 'Open WBS Builder' },
  durations: { label: 'A duration for every work package', step: 'wbs', action: 'Open WBS Builder' },
  estimates: { label: 'Confirmed AI estimates', step: 'wbs', action: 'Open WBS Builder' },
  team: { label: 'Team members', step: 'constraints', action: 'Open Constraints' },
  deadline: { label: 'An answer to the deadline question', step: 'constraints', action: 'Open Constraints' },
  specialist: { label: 'An answer to the specialist question', step: 'constraints', action: 'Open Constraints' },
}

/** The missing inputs named by a 422 from the run endpoint (unknown keys are skipped). */
export function missingInputHints(error: ApiError): MissingInputHint[] {
  const reported = error.extensions.missing
  if (!Array.isArray(reported)) return []
  return reported.flatMap((key) =>
    typeof key === 'string' && key in MISSING_INPUT_HINTS ? [{ key, ...MISSING_INPUT_HINTS[key] }] : [],
  )
}
