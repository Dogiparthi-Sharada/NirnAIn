/**
 * Step 6, Optimization Results: wire types, queries and mutations.
 *
 * Why: the results screen needs more than the latest run — the confirmation sheet
 * (`GET …/optimization/inputs`), runs started with a remedy, the specialist-rate confirmation,
 * apply and export. They live here (not in `types.ts`/`queries.ts`) so this slice stays
 * self-contained; the shared run hooks (`useLatestRun`, `useApplyRun`, `useConstraints`,
 * `useSaveConstraints`) are reused from `queries.ts` and share their cache keys.
 *
 * How: every key sits under `['projects', id, 'optimization']`, which the input screens
 * (WBS, Constraints) invalidate whenever a solver input changes, so the sheet refreshes itself.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDownload, apiFetch, isApiError, type ApiError } from './api'
import type { DeadlineAnswer, PolicyInputs } from './constraintPolicy'
import { queryKeys } from './queries'
import type { ConstraintSet, OptimizationRun, RunResult } from './types'

/* ───────────────────────────── Types ───────────────────────────── */

/** Where a solver input came from. Inferred values never reach the solver, so there is no 'inferred'. */
export type InputSource = 'stated' | 'confirmed' | 'needs_you' | 'default' | 'assumed' | 'yours'

/** One row of the confirmation sheet ("Check the inputs the solver will use"). */
export interface InputRow {
  key: string
  label: string
  value: string
  source: InputSource
  /** Where the value comes from: "Step 4", "Question on step 5", or the quoted sentence from the notes. */
  from: string
}

/** The confirmation sheet: every input the next run will use, and whether it may run. */
export interface OptimizationInputs {
  rows: InputRow[]
  ready: boolean
  /** Plain-English reasons the run is blocked, e.g. "Answer the deadline question on step 5". */
  blocking: string[]
  missing?: string[]
  /** Fingerprint of exactly these inputs (shown as "Inputs locked · hash …"). */
  hash?: string
}

/** The two ways out of an infeasible plan the backend can price. */
export type RemedyId = 'specialist' | 'move_deadline'

/** A priced remedy for an infeasible run ("What it would take"). */
export interface Remedy {
  id: RemedyId
  title: string
  detail: string
  costCents: number | null
  /** ISO date go-live would land on with this change. */
  completionDate: string | null
}

/** One work package on the timeline (ISO dates, inclusive). */
export interface ScheduleItem {
  wbsNodeId: string
  code: string
  name: string
  deliverableCode: string
  deliverableName: string
  start: string
  end: string
  baselineStart?: string | null
  baselineEnd?: string | null
  assignee?: string | null
}

/** An outside specialist the plan books. */
export interface SpecialistBooking {
  name: string
  hours: number
  rateCents: number
  costCents: number
}

/** Plain numbers behind the headline: dates, cost, peak load and the soft limits the plan bends. */
export interface RunSummary {
  startDate: string
  completionDate: string
  deadline: string | null
  deadlineMode: string
  daysLate: number
  costCents: number
  budgetCents: number | null
  peakUtilization: { name: string; percent: number } | null
  specialists: SpecialistBooking[]
  softViolations: string[]
}

/** The run result with the fields the approved design adds (all optional on older runs). */
export interface PlanResult extends RunResult {
  schedule?: ScheduleItem[]
  summary?: RunSummary | null
  remedies?: Remedy[]
}

/** How a run was set up (stored by the backend in `optimization_runs.options`). */
export interface RunOptions {
  remedy?: RemedyId | null
  /** Hash of the inputs the run was approved with, when the backend records it. */
  inputsHash?: string | null
  changeRequestId?: string | null
  baselineRunId?: string | null
}

/** An optimization run as step 6 reads it. */
export interface PlanRun extends Omit<OptimizationRun, 'result'> {
  result: PlanResult | null
  /** Plan version (1, 2, …) once applied; null while it is only a proposal. */
  version?: number | null
  options?: RunOptions | null
}

/** Body of `POST …/optimization/runs` used by this screen. */
export interface RunRequest {
  remedy?: RemedyId
}

/** The constraint set including the answers to the step 5 questions. */
export interface ConstraintAnswers extends ConstraintSet {
  specialistAnswer?: 'yes' | 'no' | 'unknown' | null
  deadlineAnswer?: DeadlineAnswer
  specialistRateConfirmed?: boolean
  contractorRateCents?: number | null
  allowContractors?: boolean
}

/** A skill on the project's list and who on the team has it. */
export interface ProjectSkill {
  skillId: string
  name: string
  required: boolean
  holders: string[]
}

export type ExportFormat = 'pdf' | 'msproject'

/** Rate the backend uses for an outside specialist when Constraints has none ($175/h). */
export const DEFAULT_SPECIALIST_RATE_CENTS = 17_500

/* ───────────────────────────── Queries ───────────────────────────── */

const optimizationKeys = {
  inputs: (projectId: string) => ['projects', projectId, 'optimization', 'inputs'] as const,
  skills: (projectId: string) => ['projects', projectId, 'skills'] as const,
  planVersions: (projectId: string) => ['projects', projectId, 'plan-versions'] as const,
}

function projectPath(projectId: string, suffix: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}${suffix}`
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
 * A 409 means a run is already going: refetch so the screen polls that one instead.
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

/** "3f9a1c…e21b": enough of the inputs hash to recognise it. */
export function shortHash(hash: string | null | undefined): string | null {
  return hash ? `${hash.slice(0, 6)}\u2026${hash.slice(-4)}` : null
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
