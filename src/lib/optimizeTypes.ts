/**
 * Wire types of step 6, Optimization Results: the confirmation sheet, runs, results and remedies.
 *
 * Purpose: Mirrors the optimizer contract the results screen reads (input rows and their sources,
 * run requests and options, schedules, specialist bookings and priced remedies) so the screen and
 * its cards share one definition.
 * How it fits: Re-exported by `lib/optimize.ts`, which screens import from; types only, no runtime
 * code; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import type { DeadlineAnswer } from './constraintPolicy'
import type { ConstraintSet, OptimizationRun, RunResult } from './types'

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
  /** Fingerprint of the inputs of the plan in force; null before one is applied (absent on older servers). */
  appliedHash?: string | null
}

/**
 * The ways to keep the target date the backend prices: bring in a contractor, let the people on
 * the late work go up to 25% over their hours, or accept the later date.
 */
export type RemedyId = 'specialist' | 'overtime' | 'move_deadline'

/**
 * A priced, re-solved way to keep the target date: offered when a plan finishes late or can't be
 * built ("What it would take"). For a late plan, `costCents` is the extra cost over that plan.
 */
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
  /**
   * The date the plan tries to keep: the completion date of the plan in force for a step 7
   * re-plan, otherwise the must-finish-by date (absent on older servers).
   */
  targetDate?: string | null
  /** Working days the plan finishes after `targetDate`; 0 when on time (absent on older servers). */
  slipDays?: number
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
  /** What-if changes for a change-request re-plan (shape owned by the Changes step). */
  overrides?: Record<string, unknown> | null
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
  /** The inputs `hash` the approved sheet showed: the server refuses (409) when the inputs changed since. */
  reviewedHash?: string
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
